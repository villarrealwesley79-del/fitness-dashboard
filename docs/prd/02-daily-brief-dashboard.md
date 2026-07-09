# Daily Brief Dashboard — PRD

> **Sources:** `README.md`; `docs/VISION.md`; `docs/PRD.md`; `docs/CURRENT_STATE.md`; `templates/index.html`; `static/js/app.js`; `static/js/app-loader.js`; `static/js/sw.js`; `app.py`; `auth.py`; `tests/test_dashboard_render_contract.py`; `tests/test_dashboard_retry_contract.py`; `tests/test_fit166_ai_coach_headline_contract.py`; `tests/test_fit177_ai_coach_headline_announcement.py`; `tests/test_fit178_ai_coach_headline_runtime.py`; `tests/test_fit139_refresh_events.py`; `tests/test_fit139_refresh_ui.py`; `tests/test_fit152_linechart_nonnegative_y.py`; `tests/test_fit192_accessibility_contract.py`; route inventory scratchpad; existing open issue scratchpad.
> **Routes:** `/`, `/api/dashboard`, `/api/recommendation/smart`, `/api/next-workout`, `/api/freshness`, `/api/vitals`, `/api/oura/status`, `/api/oura/trends`, `/api/oura/sleep-summary`, `/api/whoop/status`, `/api/wearable-sources`, `/api/food-log-refresh-events`, `/api/food-log-refresh-events/<event_id>/ack`, `/api/workout-adaptation-events`, `/api/workout-adaptation-events/<event_id>/ack`, `/manifest.json`, `/sw.js`.
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

## 1. Overview

The Daily Brief Dashboard is the first screen of the app. It exists to answer "what should I do today?" before the owner has to inspect charts, settings, or history. It combines readiness, wearable freshness, today's recommended workout, food/macros, short insight copy, and two optional trend charts into a single decision surface.

The dashboard is not a pure reporting page. Loading `/api/dashboard` can generate or refresh the server-canonical `LAST_WORKOUT_RECOMMENDATION`, apply due workout-adaptation windows, and return the current next workout. The separate `/api/freshness` endpoint exists for settings/status refreshes that need wearable staleness without mutating the workout plan.

Recommendation-engine internals belong to PRD 11. This PRD documents what the user sees, which endpoints feed those visible fields, how placeholders/retries behave, and how freshness or missing data changes the surface.

## 2. User-Facing Surfaces

| Surface | Location | Purpose | Data source | States |
| --- | --- | --- | --- | --- |
| App shell | `/`, `templates/index.html` | Authenticated PWA shell with 8 tabs and bottom nav. | Server-rendered HTML plus deferred `app-loader.js` and `app.js`. | Authenticated shell, login redirect, reload-required 401, offline 503 shell from service worker. |
| Greeting | Dashboard top | Friendly day entry point. | Client local clock. | Morning/afternoon/evening copy [TBC: exact greeting variants are client-only and not asserted in tests]. |
| Readiness card | Dashboard card | Shows readiness gauge plus HRV, resting HR, sleep duration. | `/api/oura/status`, fallback readiness from `/api/dashboard.recomp_command.readiness`. | Placeholder, loaded, retry chip, no gauge when readiness absent. |
| AI Recommendation card | Dashboard card | Shows today's plan headline, confidence, intensity/focus, time, avoid list, source attribution, primary actions. | `/api/dashboard`, `/api/recommendation/smart`, freshness nodes. | Placeholder, loaded, stale/missing wearable copy, source conflict banner, retry chip. |
| Freshness chips | Recommendation card | Shows whether WHOOP, Oura, Apple Health, and Food are trustworthy today. | `/api/dashboard.freshness`, `/api/recommendation/smart.freshness`, merged WHOOP status. | Fresh, aging/stale/missing, pending food review, over/under food target. |
| Recommendation sources strip/drawer | Recommendation card and modal | Explains which wearable/recommendation sources were used, stale, missing, or conflicted. | `recommendation_sources`, `wearable_sources`, WHOOP status. | Waiting, populated, conflict visible, modal drawer. |
| Today's macros card | Dashboard card | Shows calories/macros and food-driven context for the daily plan. | `/api/dashboard.nutrition_today` plus food-log refresh events. | Empty, loaded totals, pending review, over/under target, offline meal composer state. |
| Workout adaptation notice | Dashboard passive notice | Confirms an applied same-day food-driven workout update. | `/api/workout-adaptation-events`. | Hidden, applied update card, collapsed details, dismiss/ack retry. |
| Today at a glance | Dashboard grid | Steps, active calories, sleep, weight. | `/api/oura/status` for steps, active calories, and sleep plus `/api/dashboard.body_stats` for weight. | Placeholder or loaded values. |
| Insight card | Dashboard card | Short coaching insight plus sparkline. | `/api/oura/sleep-summary`, `/api/recommendation/smart`. | Gathering placeholder, loaded, retry chip, stale reset. |
| Quick trends | Closed-by-default `<details>` | Secondary readiness and volume charts. | `/api/oura/trends`, `/api/history-all`. | Hidden/collapsed, not enough data, chart with takeaway. |
| AI coach status | Header popover and Settings headline | Indicates local model health. | `/api/ai/health`, `/api/ai/metrics`. | Checking, primary ready, fallback active, offline. Detailed behavior belongs to PRD 11. |

## 3. Field Inventory

### Dashboard Shell and Load State

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `currentTab` | client state enum | yes | `tab-dashboard` | Must match a tab panel id. | Selected top-level app section. |
| `dashboardRenderGen` | integer | yes | `0` | Incremented per render. | Drops stale dashboard fetch completions. |
| `dashboardSentinelGen` | object | yes | `{ouraError:0,recoError:0,ouraSleepError:0}` | Incremented per render/retry. | Prevents old failed requests from re-showing retry chips after a newer retry succeeds. |
| `DASHBOARD_FETCH_TIMEOUT_MS` | integer milliseconds | yes | `30000` | Used by dashboard fetchers. | Hung dashboard endpoints fail visibly instead of leaving cards silent. |
| `CSRF_HEADER_NAME` | string | yes | `X-Requested-With` | Sent with browser API calls. | Browser mutation/read wrapper uses same-origin AJAX marker. |
| `CSRF_HEADER_VALUE` | string | yes | `XMLHttpRequest` | Sent with browser API calls. | Matches server CSRF header contract. |

### Readiness Card

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `readiness` | number or null | no | null | Uses Oura `readiness` first, then dashboard `recomp_command.readiness`; `0` is valid. | Current recovery readiness score. |
| `readiness-gauge-svg` | DOM region | yes | empty | Gauge paints only when `readiness != null`; clears when null. | Prevents fake 0% readiness and stale prior-session rings. |
| `dash-hrv` | string | no | `--` | Formats as `{value} ms`. | Current HRV signal. |
| `dash-rhr` | string | no | `--` | Formats as `{value} bpm`. | Current resting heart rate. |
| `dash-sleep` | string | no | `--` | Formats minutes into `Xm` or `Xh Ym`. | Sleep duration summary. |
| `readiness-retry` | button | yes | hidden | Shown when Oura status fetch fails. | Lets the user retry readiness without reloading the app. |

### Recommendation Card

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `reco-title` | string | no | `—` | Only writes when real title exists or stale/missing copy is deliberate. | The action headline, such as a workout focus or conservative rest recommendation. |
| `reco-confidence-pct` | string | no | `--%` | Gated on real confidence/readiness; no 45% fallback on cold open. | User-facing confidence label. |
| `reco-intensity` | string | no | `—` | Combines workout focus and smart recommendation intensity only when present. | High-level training intensity. |
| `reco-time` | string | no | hidden | Shows `{estimated_minutes} min` when present. | Expected workout duration. |
| `reco-rpe` | string | no | hidden | [TBC] Dashboard card tries `nw.goal.rpe_target`, but backend `next_workout.goal` is a string goal id, not the goal details object. | Intended RPE chip on the brief card. |
| `reco-last-session` | string/chips | no | hidden | Shows recent completion only when `last_completed.hours_ago` exists. | Explains how a recent workout affects today's plan. |
| `reco-avoid` | chip list | no | hidden | Max 3 chips; DOM textContent, not raw HTML. | Muscles to avoid due to soreness or recent training. |
| `reco-why` | string | no | "Analyzing your readiness, sleep, and training load..." | Resets placeholder and clears low-confidence styling when absent. | Plain-language reason for the recommendation. |
| `btn-start-workout` | button | yes | enabled | Starts active workout from `/api/next-workout`, with cached fallback. | Primary action. |
| `btn-adjust-plan` | button | yes | enabled | Opens adjustment flow. | Lets owner provide constraints. |
| `reco-retry` | button | yes | hidden | Covers both `/api/dashboard` and `/api/recommendation/smart` failures. | User-triggered retry for recommendation card. |

### Freshness and Source Attribution

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `freshness.open_wearables.status` | enum | yes | `missing` when not connected | `fresh`, `stale`, `missing` bucket derived from hub status. | Whether Open Wearables hub can inform the plan. |
| `freshness.open_wearables.hub_status` | string | no | null | From public OW status. | Detailed hub status. |
| `freshness.oura.status` | enum | yes | computed | See enums. | Oura freshness bucket. |
| `freshness.oura.source` | enum | yes | `cached` | `live` if last Oura sync attempt is under 1 hour old, else `cached`. | Whether readiness is from recent sync or cached data. |
| `freshness.whoop.status` | enum | no | computed | WHOOP-specific freshness. | WHOOP data trust state. |
| `freshness.apple_health.status` | enum | yes | computed | Based on latest accepted Health Auto Export evidence. | Apple Health trust state. |
| `freshness.food.status` | enum | yes | computed | Based on food log activity. | Food logging trust state. |
| `freshness.food.pending_review` | boolean | yes | false | True when unaccepted estimates exist. | Food entries may not count yet. |
| `freshness.food.target_state` | enum | yes | `none` | `none`, `under`, `on_track`, `over`. | Macro target state shown in food chip. |
| `recommendation_sources` | object | no | waiting copy | Rendered in strip/drawer. | Explains source use, conflicts, and degraded confidence. |
| `wearable_sources` | object/list | no | empty | Rendered in source surfaces. | Shows source-specific wearable contribution. |

### Nutrition/Macro Card

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `nutrition_today.calories` | integer | no | 0 | Non-negative. | Calories logged today. |
| `nutrition_today.protein_g` | number | no | 0 | Non-negative. | Protein logged today. |
| `nutrition_today.carbs_g` | number | no | 0 | Non-negative. | Carbs logged today. |
| `nutrition_today.fat_g` | number | no | 0 | Non-negative. | Fat logged today. |
| `nutrition_today.sodium_mg` | integer | no | 0 | Non-negative. | Sodium context for recovery/weight interpretation. |
| `nutrition_today.calories_target`, `.protein_target_g`, `.carbs_target_g`, `.fat_target_g` | numbers | no | settings defaults | From settings. | Daily nutrition targets. |
| `nutrition_today.calories_remaining`, `.protein_gap_g`, `.carbs_remaining_g`, `.fat_remaining_g` | numbers | no | derived | May be negative when over target. | Remaining day budget. |
| `nutrition_today.*_pct`, `.entries_count` | numbers | no | derived | Percent values are renderer inputs. | Macro progress and row count. |
| `nutrition_today.coaching_context.warnings` | array | no | empty | Alongside `pending_review_count` and `next_day_context`. | Food-aware coaching flags. |

### Charts

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `chart-readiness-7d` | SVG line chart | no | "Not enough data yet." | Needs at least 2 finite points. | Seven-day readiness trend. |
| `readiness-7d-avg` | string | no | `avg —` | Derived from Oura trend data. | Average readiness summary. |
| `chart-volume-4w` | SVG chart | no | no data/empty | From history volume. | Four-week training volume. |
| `chart-history-volume` | SVG line chart on History | no | "Not enough data yet." | Uses `nonNegativeY` and `emptyMaxY: 100` to avoid negative Y axis on zero-only series. | Broader volume trend. |
| `chart-takeaway` fields | string | no | hidden | Hidden when empty. | Plain-language chart takeaway. |

## 4. Interactions & Flows

### Initial Dashboard Load

Trigger -> User opens `/` or switches to Dash tab.  
Behavior -> Server returns `index.html` with `Cache-Control: no-store`; `app-loader.js` waits for window `load` and appends async `app.js`; `renderDashboard()` paints existing cached state immediately, then starts independent fetches for dashboard, Oura status, smart recommendation, and Oura sleep.  
Validation -> Each card keeps placeholders until its own data arrives; stale fetch completions are generation-guarded.  
API -> `/api/dashboard`, `/api/oura/status`, `/api/recommendation/smart`, `/api/oura/sleep-summary`; trends/history load independently.  
Success -> Cards repaint as data arrives; quick trends paint separately.  
Failure -> Failed readiness/recommendation/insight slices show their retry chips without blocking other cards.

### Retry Chips

Trigger -> User clicks `readiness-retry`, `reco-retry`, or `insight-retry`.  
Behavior -> The relevant sentinel generation increments and that slice is refetched.  
Validation -> Older pending requests cannot re-show the chip after a newer retry has succeeded.  
API -> Readiness uses `/api/oura/status`; recommendation uses `/api/dashboard` and/or `/api/recommendation/smart`; insight uses `/api/oura/sleep-summary`.  
Success -> Retry chip hides and card repaints.  
Failure -> Chip remains visible and keyboard-accessible.

### Recommendation Card Degraded Data

Trigger -> Dashboard/recommendation payload contains stale or missing wearable data.  
Behavior -> Title and why copy change to conservative/lower-confidence language. Freshness chips show source states. Source conflict banner appears when conflicts are present.  
Validation -> Placeholder is preserved when there is no real data; stale/missing messages are explicit, not silent fallbacks.  
API -> `/api/dashboard`, `/api/recommendation/smart`, `/api/whoop/status`.  
Success -> User sees whether the plan is personal or generic.  
Failure -> Retry chip covers the recommendation card.

### Macro Context

Trigger -> Dashboard load or food-log update.  
Behavior -> Macro card shows empty state when no food is logged; otherwise calories/protein/carbs/fat progress, sodium total, food context chips, and next-day context.  
Validation -> Pending estimates do not count as accepted food; photo retention copy says photos are discarded after extraction.  
API -> `/api/dashboard.nutrition_today`, food routes owned by nutrition PRDs, `/api/food-log-refresh-events`.  
Success -> Food-aware context appears in daily brief.  
Failure -> Meal composer shows inline error/offline state; dashboard remains usable.

### Workout Adaptation Notice

Trigger -> Accepted food creates a due adaptation event and dashboard surfaces poll.  
Behavior -> Client polls `/api/workout-adaptation-events?unacknowledged=true&limit=10`. The GET feed can evaluate due adaptation windows and mutate the server-canonical recommendation cache. Only applied, non-silent, same-day events render. No-change, low-confidence, and next-day events are marked seen but not shown.  
Validation -> User-visible card contains neutral reason, signal chips, and collapsed details; backend audit metadata/rules/citations are not rendered.  
API -> GET `/api/workout-adaptation-events`; POST ack endpoint.  
Success -> "Workout updated" notice appears; if an active workout is open and backend says it was updated live, the active workout merges the latest plan without losing completed sets.  
Failure -> Ack failure keeps the same card and re-enables dismiss instead of duplicating cards.

### Offline/App Shell Behavior

Trigger -> Network unavailable, service worker update, or app shell fetch fails.  
Behavior -> Service worker is network-first for HTML, JS, CSS, and APIs. It does not precache the app shell. API GET failures return JSON `{error:"Offline"}` with 503; navigation failure returns a simple offline HTML 503.  
Validation -> Old Cache Storage entries are deleted on activate. If a new service worker takes control while an active workout has progress, the app shows "Update ready after workout. Refresh when finished." instead of auto-reloading.  
API -> `/sw.js`, service-worker fetch handler.  
Success -> Fresh code loads on next open; workout input is not lost to forced reload.  
Failure -> Offline state is visible.

## 5. API Endpoints

| Method | Path | Auth | Trigger | Key params | Response shape | Real/Mock |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/` | Required unless login disabled | App shell load | none | HTML app shell, no-store headers | Real |
| GET | `/api/dashboard` | Required | Dashboard load | `active_workout_open`, `completed_sets` optional | headline, muscles, exercises, alerts, next_workout, adaptation events, readiness factors, body stats, recomp command, nutrition_today, KPIs, freshness, wearable/recommendation sources | Real |
| GET | `/api/recommendation/smart` | Required | Recommendation reasoning | `active_workout_open`, `completed_sets` optional | recommendation, readiness, effective_readiness, sleep_score, hrv, hrv_trend, avoid_muscles, reasoning, freshness, nutrition_context, next_workout, confidence_level | Real |
| GET | `/api/next-workout` | Required | Start Workout / Workout tab | `active_workout_open`, `completed_sets` optional | next_workout, workout_adaptation_events, recommendation_sources | Real |
| GET | `/api/freshness` | Required | Settings/status freshness | none | `{freshness}` only; side-effect-free | Real |
| GET | `/api/vitals` | Required | Vitals tab | none | vitals/body/activity payload | Real |
| GET | `/api/oura/status` | Required | Readiness card/settings | `refresh=true` optional | Oura connection/cache/readiness status | Real |
| GET | `/api/oura/trends` | Required | Quick trends | none | trend rows | Real |
| GET | `/api/oura/sleep-summary` | Required | Insight/readiness | none | sleep summary | Real |
| GET | `/api/whoop/status` | Required | Freshness/source merge | none | WHOOP connection/freshness | Real |
| GET | `/api/wearable-sources` | Required | Source attribution | none | `sources` array | Real |
| GET | `/api/food-log-refresh-events` | Required | Passive food refresh notices | `unacknowledged`, `since`, `limit` | events/count | Real |
| POST | `/api/food-log-refresh-events/<event_id>/ack` | Required + CSRF/same-origin | Dismiss food refresh notice | path id | status/id | Real |
| GET | `/api/workout-adaptation-events` | Required | Dashboard adaptation notices | `unacknowledged`, `since`, `limit`, `active_workout_open`, `completed_sets` | events/count | Real |
| POST | `/api/workout-adaptation-events/<event_id>/ack` | Required + CSRF/same-origin | Dismiss workout update | path id | status/id | Real |
| GET | `/manifest.json` | Public | PWA install metadata | none | manifest JSON | Real |
| GET | `/sw.js` | Public | Service worker registration | none | JS service worker | Real |

`/api/dashboard`, `/api/recommendation/smart`, `/api/next-workout`, and GET `/api/workout-adaptation-events` can all evaluate due workout adaptation windows and mutate the server-canonical recommendation cache when not blocked by an active-workout-open request without completed set counts. `/api/freshness` is the explicit read-only alternative for freshness-only consumers.

Pre-boot dashboard HTML can show empty/default copy such as "No food logged today." and placeholder dashes until `app.js` loads and paints real state. A reload-required 401 during active workout progress shows "Update ready after workout. Refresh when finished." instead of reloading; plain 401s redirect to `/login?next=...`.

## 6. Data Model & Persistence

| Store | File/table | Used by daily brief | Notes |
| --- | --- | --- | --- |
| Workout JSON | `DATA_DIR/data_workouts.json` | Volume, history, recent completion, next workout, adherence context. | Loaded into `WORKOUTS`; malformed JSON is moved aside and default recreated. |
| Soreness JSON | `DATA_DIR/data_soreness.json` | Readiness, avoid list, recovery signal. | Recent soreness is time-decayed by helpers. |
| Cardio JSON | `DATA_DIR/data_cardio.json` | Cardio fatigue and recommendation context. | Also fed by completed-workout cardio. |
| Recovery JSON | `DATA_DIR/data_recovery.json` | Recovery bonus. | Sauna/cold plunge/etc. |
| Body JSON | `DATA_DIR/data_body.json` | Weight/body trend in glance and body stats. | Local date strings. |
| Nutrition JSON/SQLite food logs | `DATA_DIR/data_nutrition.json`, SQLite food log tables | Macro card, nutrition context, adaptation windows. | Accepted logs count; pending review does not. |
| Oura SQLite | `DATA_DIR/oura_daily.sqlite3` | Readiness, HRV, sleep, freshness. | Daily cache and trend range. |
| WHOOP SQLite | `DATA_DIR/whoop.sqlite3` | WHOOP freshness/source modifiers. | Normalized facts; token material handled outside normal responses. |
| Open Wearables config/facts | `DATA_DIR/open_wearables_config.json`, `DATA_DIR/wearable_facts.sqlite3` | Source attribution and readiness guards. | Sync responses are metadata-only. |
| Service worker cache | Browser Cache Storage | App shell update behavior. | Old caches deleted; no app shell/API cache retained. |

`DATA_DIR` comes from `runtime_config.data_path`; when unset, runtime files live next to the app. The public repo intentionally excludes runtime data.

## 7. Enums & Constants

| Name | Values | Meaning |
| --- | --- | --- |
| Dashboard fetch timeout | `30000` ms | Per-card dashboard fetch timeout. |
| Retry chips | `readiness-retry`, `reco-retry`, `insight-retry` | Readiness, recommendation, and insight retry controls. |
| Freshness source labels | `live`, `cached`, `hub` | Oura is `live` only when last sync attempt is under 1 hour old; Open Wearables source is `hub`. |
| Freshness buckets | `fresh`, `aging`, `stale`, `missing` plus WHOOP-specific states | Trust labels for wearable data. Exact per-source computation lives in integration helpers. |
| Food target states | `none`, `under`, `on_track`, `over` | Macro status for daily food chip. |
| Smart recommendation values | `intensity`, `moderate`, `recovery` plus fallback strings | Dashboard maps these to High/Moderate/Low display. |
| Readiness command | `TRAIN`, `RECOVER` | Dashboard `recomp_command.signal`; TRAIN requires readiness >= 70 and max soreness < 7. |
| Wearable degraded title states | stale, all missing | Stale shows generic recommendation; all missing shows rest/conservative copy. |
| Service worker cache name | `fitness-dashboard-v20260627-fit249-auto-mobile-invite` | Versioned worker; activate deletes all old caches. |
| App bundle version | `20260627-fit249-auto-mobile-invite` | Used by `app-loader.js` and template. |
| Chart minimum data | 2 finite points | Otherwise line chart renders "Not enough data yet." |
| Non-negative chart options | `nonNegativeY`, `emptyMaxY: 100` | Prevent zero-only volume charts from drawing negative domains. |

## 8. Integration Points

| Feature | Coupling |
| --- | --- |
| Workout Planning & Execution ([03-workout-planning-execution.md](03-workout-planning-execution.md)) | Dashboard owns first-screen start action and surfaces `next_workout`; workout execution owns active-workout lifecycle. |
| AI Coach PRD 11 | Dashboard shows smart recommendation reasoning and AI status; AI internals and prompt contracts live elsewhere. |
| Nutrition/Food PRDs | Macro card, pending review, food refresh notices, and adaptation triggers depend on accepted food logs. |
| Wearables PRDs | Oura, WHOOP, Apple Health, and Open Wearables freshness/status drive confidence and source attribution. |
| Settings | `/api/freshness` is designed for settings to avoid dashboard plan mutation. |
| Push notifications | Reminder preview uses the same freshness data for stale wearable/pending food nudges. |

## 9. Permissions & Security

All dashboard APIs are session-authenticated by default through Flask-Login unless `LOGIN_DISABLED` is set in testing. Browser API calls include `X-Requested-With: XMLHttpRequest`; mutating routes require that header, a valid form CSRF token, or same-origin browser metadata. Cross-origin browser mutations are rejected before CSRF fallback. Token-authenticated Apple Health sync and Stripe webhook are separate exemptions, not part of this daily brief surface.

The app is single-owner by default. `FITNESS_DASHBOARD_SINGLE_USER=false` disables the owner-only guard; otherwise the minimum user id or `FITNESS_DASHBOARD_OWNER_USER_ID` is the owner. Runtime data is local and excluded from Git. Normal dashboard responses should not expose raw wearable payloads, Open Wearables hub secrets, WHOOP token material, or raw food photos.

## 10. Business Rules

- The daily brief must answer the training decision first; charts are secondary and quick trends are collapsed by default.
- The readiness gauge must not invent a zero reading. Missing readiness leaves the gauge empty.
- Recommendation placeholders must reset on null state to avoid prior-session stale guidance.
- `/api/dashboard` is not read-only with respect to the active recommendation cache; use `/api/freshness` for freshness-only views.
- Dashboard and smart recommendation share the same food-aware hard-workout warning context.
- Stale or missing wearable data lowers recommendation confidence and must be visible in copy/chips.
- Accepted food can schedule workout adaptation; pending-review food cannot.
- Same-day applied adaptation notices are visible; next-day/no-change/low-confidence events stay hidden from the dashboard.
- Service worker must fail visibly rather than serving stale HTML/JS/API data.
- Active workout progress blocks automatic service-worker reload.
- Dashboard fetchers must not let a slow endpoint block unrelated cards.

## 11. Config & Environment

| Config | Default | Daily brief behavior |
| --- | --- | --- |
| `DATA_DIR` | app-local path | Location for JSON/SQLite runtime stores. |
| `SECRET_KEY` | environment required in production; local fallback may exist | Session security. |
| `FITNESS_DASHBOARD_SINGLE_USER` | `true` | Owner-only data access by default. |
| `FITNESS_DASHBOARD_OWNER_USER_ID` | first local user id | Owner id override. |
| `OURA_API_TOKEN` | unset | Oura status/sync availability. |
| `HEALTH_SYNC_TOKEN` | unset | Apple Health webhook auth; status remains auth-gated. |
| `OW_*` variables | unset | Open Wearables hub setup/status. |
| `WHOOP_*` variables/files | unset | WHOOP status and source contribution. |
| `LM_STUDIO_*` variables | unset | AI coach health/reasoning availability. |

## 12. Test Coverage

| Test file | Coverage |
| --- | --- |
| `tests/test_dashboard_render_contract.py` | Guarded placeholders, readiness gauge clearing, recommendation card stale state, source summary wiring. |
| `tests/test_dashboard_retry_contract.py` | Retry chips, independent dashboard fetch chains, next-workout fast endpoint, app bundle deferral, service-worker cache-bust behavior. |
| `tests/test_fit166_ai_coach_headline_contract.py` | AI coach headline contract. |
| `tests/test_fit177_ai_coach_headline_announcement.py` | AI headline live announcement. |
| `tests/test_fit178_ai_coach_headline_runtime.py` | AI headline runtime states. |
| `tests/test_fit139_refresh_events.py` and `tests/test_fit139_refresh_ui.py` | Food-log refresh notice contract and dashboard/history hook. |
| `tests/test_fit152_linechart_nonnegative_y.py` | Non-negative Y axis and zero-volume chart domain. |
| `tests/test_fit192_accessibility_contract.py` | Tab ARIA, modal semantics/focus, service-worker version contract. |
| `tests/test_hard_workout_nutrition_context.py` | Dashboard and smart recommendation share food warning context. |
| `tests/test_wearable_freshness_contract.py` | Freshness-only endpoint avoids recommendation mutation. |

Coverage gaps: many tests assert source strings rather than browser behavior; there is limited full DOM/runtime coverage for actual dashboard rendering after mixed endpoint failures; [TBC] exact visual behavior of the source drawer and macro-card warnings needs browser QA.

## 13. Gaps & Issue Candidates

### IC-1: Split dashboard read from recommendation mutation
- **Type:** Improvement
- **Priority:** high
- **Where:** `app.py:4924`, `app.py:8392`, `app.py:13819`, `/api/dashboard`, `/api/workout-adaptation-events`, `/api/freshness`
- **Problem:** `/api/dashboard` both renders the brief and mutates `LAST_WORKOUT_RECOMMENDATION`; settings already had to add `/api/freshness` to avoid this side effect. The first screen still couples a read-heavy dashboard refresh to recommendation generation/adaptation work.
- **Why it matters:** A status refresh can unexpectedly affect the plan the workout tab is about to execute and can keep the first screen slow.
- **Acceptance criteria:**
  - Dashboard has a read-only path for non-plan cards.
  - Plan mutation happens only through explicit recommendation/workout endpoints.
  - Existing freshness-only behavior remains side-effect-free.
  - Contract tests verify dashboard status refresh cannot reset an adjusted/swapped plan.
- **Duplicate-of:** none
- **Relates-to:** FIT-262 (hot-path performance on the same endpoints)

### IC-2: Move dashboard render contracts from string asserts to DOM tests
- **Type:** Test
- **Priority:** medium
- **Where:** `tests/test_dashboard_render_contract.py`, `static/js/app.js:2806`
- **Problem:** Core dashboard safety behavior is protected mainly by source-string assertions. These catch known regressions but do not prove the browser-visible result when fetches resolve in different orders.
- **Why it matters:** The daily brief can regress visually while preserving the asserted strings.
- **Acceptance criteria:**
  - Add a browser/JS fixture that simulates mixed dashboard endpoint success/failure.
  - Verify placeholders, retry chips, stale clearing, and source conflict UI in DOM.
  - Keep focused source-contract tests only where runtime simulation is impractical.
- **Duplicate-of:** FIT-264

### IC-4: Fix or remove the dashboard RPE chip contract
- **Type:** Bug
- **Priority:** low
- **Where:** `static/js/app.js:2964`, `app.py:3650`, `/api/dashboard`
- **Problem:** The dashboard recommendation card tries to read `nw.goal.rpe_target`, but backend `next_workout.goal` is a goal id string while per-exercise RPE targets live on exercise rows. The chip may stay hidden even when a meaningful target exists.
- **Why it matters:** The brief promises training intensity, and RPE is a useful first-screen signal.
- **Acceptance criteria:**
  - Decide whether the dashboard should show average exercise RPE, goal RPE, or no RPE chip.
  - Backend/JS contract exposes the chosen value consistently.
  - Add a test for a plan with exercise RPE targets.
- **Duplicate-of:** none

### IC-5: Add offline dashboard state QA beyond service-worker fallback
- **Type:** Test
- **Priority:** medium
- **Where:** `static/js/sw.js:1`, `static/js/app.js:149`, dashboard render paths
- **Problem:** The service worker returns visible 503 responses offline, but dashboard card-level offline behavior is not comprehensively tested. Existing coverage focuses on retry chips and stale cache prevention.
- **Why it matters:** The owner may open the PWA in the gym with intermittent connectivity and needs clear degraded states.
- **Acceptance criteria:**
  - Simulate offline API failures for dashboard, Oura, smart recommendation, and trends.
  - Verify retry chips, placeholders, and no stale prior-session guidance.
  - Verify active workout progress prevents forced reload during service-worker update.
- **Duplicate-of:** none

### IC-6: Reconcile macro-card blank shell and loader timing risks
- **Type:** Bug
- **Priority:** medium
- **Where:** `templates/index.html:118`, `static/js/app-loader.js:1`, `static/js/app.js` macro renderers
- **Problem:** The dashboard relies on deferred bundle boot and client rendering for macro state. Existing open frontend debt calls out blank shell and loader timing risks, which can affect the first-screen food/macros section.
- **Why it matters:** Food-aware coaching loses trust if the first screen shows a blank or stale nutrition block.
- **Acceptance criteria:**
  - Macro card has deterministic loading, empty, error, and accepted-food states.
  - Deferred loader timing cannot leave the macro card permanently blank.
  - Add focused browser or JS DOM tests for macro card boot.
- **Duplicate-of:** FIT-263
