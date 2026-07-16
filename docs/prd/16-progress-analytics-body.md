# Progress Analytics, Body Composition & Sleep Tracking — PRD

> **Sources:** `README.md`; `docs/VISION.md`; `docs/PRD.md`; `docs/CURRENT_STATE.md`; `app.py` lines 196-207, 430-465, 1208-1220, 2282-2284, 2495-2765, 2835-2863, 3954-4255, 4274-4373, 4376-4455, 5181-5390, 8549-8623, 10126-10170, 14495-14566, 15367-15927; `history_normalization.py`; `data_loader.py`; `templates/index.html` lines 580-740; `static/js/app.js` lines 134-168, 1120-1265, 5035-5365, 5517-5524, 6900-6995; `auth.py` lines 55-75, 364-525; tests listed in section 12.
> **Routes:** `/api/add-body-measurement`, `/api/body-history`, `/api/body-recomp`, `/api/body/navy-calc`, `/api/sleep/import`, `/api/sleep/analytics`, `/api/insights`, `/api/adherence`, `/api/muscle-fatigue`, `/api/acwr`, `/api/progressive-overload`, `/api/analytics/advanced`, `/api/vitals`, `/api/weather`, `/api/export-md`
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

## 1. Overview

This feature owns the owner's progress loop after the daily decision has been made: body measurement logging, body-recomposition trend review, manual/local sleep analytics, training load, adherence, muscle recovery, progressive overload, vitals, weather context, and a Markdown workout export. It answers "Is the plan working?", "What changed in my body?", "Am I recovering?", and "Did I actually follow the recommendation?"

The surfaces are local-first and mostly deterministic. Body and manual sleep data are JSON-backed under `DATA_DIR`; workout analytics read the persisted workout, soreness, cardio, recovery, settings, Oura, WHOOP, Open Wearables, and Apple Health-derived stores where relevant. Oura-specific sleep sync and daily brief behavior are owned by other PRDs; this PRD documents how this progress layer consumes or falls back to those sources.

The current implementation is real but uneven. Some endpoints are polished dashboard inputs (`/api/vitals`, `/api/muscle-fatigue`, `/api/insights`), while others are backend-only or lightly surfaced (`/api/sleep/import`, `/api/sleep/analytics`, `/api/progressive-overload`, `/api/export-md`). Where code implies product intent but no UI consumes it, this PRD marks the gap rather than treating the intent as shipped.

Cross-links: daily first-screen recommendation context belongs in PRD 02; Oura sleep/readiness sync belongs in PRD 07; workout prescription and adaptive plan logic belong in PRD 11.

## 2. User-Facing Surfaces

The Body tab shows the latest weight and body-fat values, recent deltas, interpretation notes from nutrition context, a target-progress card, 90-day weight and body-fat charts, composition/tape measurement slots, a 14-day nutrition trend, and a compact "Log Measurement" form. The visible body form accepts only weight in pounds and body-fat percent. The backend can store tape fields (`neck_in`, `waist_in`, `chest_in`, `hips_in`, `arms`, `legs`) and notes, but the current UI does not expose those inputs.

The Stats tab shows performance totals for the selected range, muscle recovery cells, a muscle-volume donut, and progress insight cards. The range chips are `7D`, `14D`, `30D`, `90D`, and `1Y`, with `30D` active by default. The stats totals are computed client-side from `/api/history-all` plus Apple Health workout rows; the recovery cells come from `/api/muscle-fatigue`; the progress insight cards come from `/api/insights`.

The Vitals tab consumes `/api/vitals` for weight, heart-rate, sleep, and activity cards. The Settings tab consumes `/api/weather` to show current weather status in the data-sources/integrations area. Dashboard and smart recommendation code use only cached weather context, so live weather fetches do not block the daily brief.

Manual sleep import and sleep analytics are backend endpoints with no first-class visible import UI found in the current `templates/index.html`/`static/js/app.js` scan. They coexist with Oura and Apple Health sleep surfaces but are not currently presented as a source-management experience.

The Markdown export route is intended for workout history export. The current UI download button uses `/api/export-backup`, not `/api/export-md`; no direct app-shell trigger for `/api/export-md` was found.

## 3. Field Inventory

### Body Measurement Form and Payload

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `weight_lbs` | number | Yes by API | none | 50.0 to 1000.0 inclusive | Scale weight in pounds. The visible UI sends `null` when blank, but the API rejects missing/null because weight is not `allow_none`. |
| `body_fat_pct` | number or null | No | `null` | 1.0 to 60.0 inclusive when present | Body-fat estimate, from manual entry or another calculator. |
| `date` | string | No | server-local `YYYY-MM-DD` | No explicit date validation in this route | Measurement day. Server defaults to current local day. |
| `neck_in` | any | No | `null` | No validation in add route | Neck tape measurement in inches when supplied by a non-UI client. |
| `waist_in` | any | No | `null` | No validation in add route | Waist tape measurement in inches. |
| `chest_in` | any | No | `null` | No validation in add route | Chest tape measurement in inches. |
| `hips_in` | any | No | `null` | No validation in add route | Hip tape measurement in inches. |
| `arms` | any | No | `null` | No validation in add route | Arm measurement payload; exact shape [TBC] because the API stores passthrough. |
| `legs` | any | No | `null` | No validation in add route | Leg measurement payload; exact shape [TBC] because the API stores passthrough. |
| `notes` | string | No | empty string | max 2000 chars | Freeform context for the measurement. Not exposed in the visible form. |
| `created_at` | ISO datetime | System | server time | generated | Audit timestamp for when the row was saved. |

### Navy Body-Fat Calculator Payload

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `sex` | string | No | `male` | Lowercased; only exact `female` uses the female formula, all other values use male formula | Determines Navy formula branch. |
| `height_in` | number | Yes | none | 48 to 96 | Height in inches. |
| `neck_in` | number | Yes | none | 8 to 30 | Neck circumference in inches. |
| `waist_in` | number | Yes | none | 18 to 80 | Waist circumference in inches. |
| `hip_in` | number | Required only when `sex == "female"` | none | 20 to 80 | Hip circumference for female formula. |
| `body_fat_pct` | number | Response | clamped | 3.0 to 60.0 after formula | Estimated Navy body-fat percentage. |

### Body History and Recomposition Response

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `history` | array | Response | `[]` | Sorted by date descending for `/api/body-history`, ascending for `/api/body-recomp` | Measurement log. |
| `weight_change` | number or null | Response | `null` | Calculated from adjacent sorted rows | Day-to-prior-entry weight delta in pounds. |
| `trend` | enum string | Response | `unknown` | Last up to 7 entries, linear slope threshold +/-0.1 | One of `increasing`, `decreasing`, `stable`, `unknown`. |
| `dates` | string[] | Response | `[]` | From body rows | X-axis for recomp charts. |
| `weight` | number[] | Response | `[]` | From `weight_lbs` | Raw weight sequence. |
| `weight_7d_avg` | number/null[] | Response | `[]` | Average of non-null weights within the trailing 7-row window | Smoothed weight trend. |
| `body_fat_pct` | number/null[] | Response | `[]` | From body rows | Body-fat sequence. |
| `lean_mass_lbs` | number/null[] | Response | `[]` | `weight_lbs - fat_mass_lbs` | Lean mass estimate. |
| `fat_mass_lbs` | number/null[] | Response | `[]` | `weight_lbs * body_fat_pct / 100` | Fat mass estimate. |
| `summary.latest` | object | Response | none | latest ascending body row | Most recent body row. |
| `summary.target_weight_lbs` | number | Response | default 175 | From settings | Target body weight. |
| `summary.target_body_fat_pct` | number | Response | default 18 | From settings | Target body-fat percentage. |
| `summary.eta_weeks` | number or null | Response | `null` | Requires target, current weight, and at least 14 weight rows | Weeks to target at current 14-entry velocity. Negative values mean trend is moving away from target; UI renders "Not on track." |

### Manual Sleep Import Payload

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `entries` | array of objects | Conditional | none | Must be list when used | Batch manual sleep rows. |
| `csv` | string | Conditional | none | Parsed by `csv.DictReader` | CSV import alternative. |
| `date` / `day` | string | Row required to import | skipped if empty | First 10 chars only | Sleep date bucket. |
| `source` | string | No | `apple_watch` | none | Source label. Dedupe prefers `apple_watch` when duplicate dates exist within the selected source set. |
| `sleep_duration_min` / `duration_min` | number-ish | No | 0 | `int(float(...))`; no range guard | Total asleep minutes. |
| `time_in_bed_min` / `in_bed_min` | number-ish | No | 0 | `int(float(...))`; no range guard | Time in bed. |
| `deep_min` | number-ish | No | 0 | `int(float(...))`; no range guard | Deep sleep minutes. |
| `rem_min` | number-ish | No | 0 | `int(float(...))`; no range guard | REM sleep minutes. |
| `light_min` | number-ish | No | 0 | `int(float(...))`; no range guard | Light sleep minutes. |
| `awake_min` | number-ish | No | 0 | `int(float(...))`; no range guard | Awake minutes. |
| `sleep_start` | string | No | null | parsed later if ISO-compatible | Bedtime timestamp for consistency score. |
| `sleep_end` | string | No | null | none in import | Wake timestamp. |

### Analytics and Vitals Payload Fields

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `acwr` | number | Response | 0.0 | Rounded to 3 decimals | Acute/chronic workload ratio. |
| `acute_load` | integer | Response | 0 | 7-day sum | Recent training load. |
| `chronic_load` | integer | Response | 0 | Weekly average over observed days up to 28 days | Baseline weekly load. |
| `risk` | enum | Response | `detraining` | See section 7 | ACWR risk band. |
| `adherence_rate` | integer percent | Response | 0 | Rounded `followed_count / linked_completions * 100` | Share of recommendation-linked completions followed. |
| `frequently_skipped` | array pairs | Response | `[]` | Top 5 by count | Exercises most often skipped from recommendations. |
| `fatigue_level` | enum | Response | derived | See section 7 | Muscle fatigue label. |
| `readiness` | number 0-10 | Response | derived | `get_readiness_score` | Muscle readiness score. |
| `volume_landmarks.zone` | enum | Response | derived | See section 7 | Weekly volume zone by muscle. |
| `fatigue_score` | number 0-100 | Response | derived | capped at 100 | Advanced composite fatigue score. |
| `deload_recommended` | boolean | Response | derived | currently `fatigue_score >= fatigue_threshold` only | The deload detector branch is dead because the route reads nonexistent key `recommended` instead of `needed`. |
| `weight.current_lbs` | number/null | Response | null | latest body row | Current weight for vitals. |
| `heart_rate.resting_bpm` | number/null | Response | null | source fallbacks | Resting HR. |
| `sleep.last_night` | object/null | Response | null | source fallbacks | Latest sleep summary. |
| `activity.steps_today` | integer/null | Response | null | source fallbacks | Steps for latest/today activity. |
| `source` | object | Response | null values | `open_wearables`, `oura`, `oura_raw`, `whoop`, or null | Provenance for heart-rate, activity, and sleep fields. |

## 4. Interactions & Flows

Body measurement logging: user opens Body tab, enters weight and optional body-fat percent, and taps Save Measurement. The client rejects a payload where both visible fields are blank, then posts JSON to `/api/add-body-measurement`. The API requires valid JSON and a valid `weight_lbs`, accepts optional `body_fat_pct` and notes, stores the entry in `BODY_DATA`, writes `data_body.json`, and returns the saved row. On success, the client clears body/dashboard cache and re-renders the Body tab. On failure, it shows "Save failed."

Body review: loading the Body tab calls `/api/body-history` through `getBody()` and `/api/body-recomp` through `renderBodyRecompTargetProgress()`. The history endpoint sorts entries newest first, adds per-entry `weight_change`, and returns a trend label from the last 7 entries. The recomp endpoint sorts oldest first, computes 7-entry rolling weight average, lean mass, fat mass, settings targets, and ETA. The UI hides target progress if there is no latest row or no target items to show.

Navy body-fat calculation: a client posts tape measurements to `/api/body/navy-calc`. The API validates height/neck/waist and, for `female`, hip, then returns a clamped body-fat percentage. No current app-shell trigger was found, so the intended visible flow is [TBC]. The likely intended flow is a body-composition helper that calculates `body_fat_pct` before saving a measurement.

Manual sleep import: a client posts either `entries[]` or `csv` text to `/api/sleep/import`. Rows without a `date` or `day` are skipped. Rows are normalized into one record per date and merged by date over existing `SLEEP_DATA`; later imported rows replace earlier rows for the same date. The API writes `data_sleep.json` and returns the number of parsed/imported entries, including entries that may replace existing dates.

Sleep analytics: `/api/sleep/analytics` reads Oura `long_sleep` rows from `oura_daily.sqlite3` table `oura_sleep` and merges them with manual `SLEEP_DATA` by date. Manual duplicates prefer `apple_watch`; an Oura row replaces a manual row only for the same date, while manual-only dates remain visible. Every returned row includes source provenance. The route calculates bedtime consistency when at least four parseable sleep-start times exist and next-day sleep-duration to average-e1RM correlation when at least three paired sleep/workout data points exist.

Stats render: opening Stats loads `/api/history-all` and Apple Health workouts for the chosen range, merges strength history sources client-side, computes visible totals and deltas client-side, then calls `/api/muscle-fatigue` and `/api/insights`. Muscle recovery cells are clickable; clicking toggles a details row with recovery label, readiness, weekly sets, last trained, soreness note, and recommendation.

ACWR: `/api/acwr` returns the server-side 28-day training-load calculation. It includes logged workouts and eligible Apple Health recommendation workouts. Strength load is reps times weight per set; if no set load exists, it falls back to `recommendation_load`, then duration minutes.

Adherence: `/api/adherence` reads persisted `WORKOUTS`, not session-local completed workouts. It counts all completed workouts, narrows adherence denominator to workouts with `recommendation_id`, counts only rows whose `adherence.followed` is exactly `true`, and reports skipped exercises from `adherence.skipped`.

Progressive overload: `/api/progressive-overload` returns top-set weight trends for a fixed list of eight major exercises. It does not compute e1RM; it tracks the highest raw `weight_lbs` per exercise per date and compares the latest two dates.

Weather: Settings calls `/api/weather` to fetch current wttr.in weather for explicit `?location`, then last cached location, then hardcoded `San_Antonio`. Dashboard and smart recommendation use `_cached_wttr` only, so they can include hot/cold context when cache is warm without making a live network call.

Markdown export: `/api/export-md` streams a `text/markdown` attachment named `workout_export.md`. It includes a summary and one table row per workout set.

## 5. API Endpoints

| Method | Path | Auth | Trigger | Key params | Response shape | Real/Mock |
|---|---|---|---|---|---|---|
| POST | `/api/add-body-measurement` | Owner session + CSRF/same-origin | Body form save or API client | JSON body measurement fields | `{status, body_measurement}` | Real |
| GET | `/api/body-history` | Owner session | Body tab load | none | `{status, history, trend}` | Real |
| GET | `/api/body-recomp` | Owner session | Body target progress card | none | `{history, dates, weight, weight_7d_avg, body_fat_pct, lean_mass_lbs, fat_mass_lbs, summary}` | Real |
| POST | `/api/body/navy-calc` | Owner session + CSRF/same-origin | Backend/API calculator; UI [TBC] | `sex`, `height_in`, `neck_in`, `waist_in`, `hip_in` | `{body_fat_pct}` | Real |
| POST | `/api/sleep/import` | Owner session + CSRF/same-origin | Backend/API import; UI [TBC] | `entries[]` or `csv` | `{status, imported}` | Real |
| GET | `/api/sleep/analytics` | Owner session | Backend/API analytics; UI [TBC] | none | `{history, consistency_score, sleep_perf_correlation}` | Real |
| GET | `/api/insights` | Owner session | Stats progress insights | none | `{insights, charts}` | Real |
| GET | `/api/adherence` | Owner session | API consumer [TBC]; tests and history surfaces depend on adherence rows | none | adherence totals | Real |
| GET | `/api/muscle-fatigue` | Owner session | Stats muscle recovery | none | object keyed by muscle | Real |
| GET | `/api/acwr` | Owner session | API consumer and recommendation factors | none | `{acwr, acute_load, chronic_load, risk, message}` | Real |
| GET | `/api/progressive-overload` | Owner session | Backend/API progress trend; UI [TBC] | none | `{exercises}` | Real |
| GET | `/api/analytics/advanced` | Owner session | Cached loader; UI direct rendering [TBC] | none | advanced analytics object | Real |
| GET | `/api/vitals` | Owner session | Vitals tab | none | `{weight, heart_rate, sleep, activity, source}` | Real |
| GET | `/api/weather` | Owner session | Settings weather status | `location` query optional | weather object | Real external provider, best-effort |
| GET | `/api/export-md` | Owner session | Direct download/API; UI trigger [TBC] | none | Markdown attachment | Real |

Error behavior is mostly shared. Invalid/missing JSON returns `400` with `{status:"error", error:{code,message}}`. Unauthenticated API calls return `401 {"error":"Unauthorized","login":"/login"}`. Non-owner calls return `403`. Mutating browser calls require the `X-Requested-With: XMLHttpRequest` header, a valid form token, or same-origin browser metadata; cross-origin browser headers are rejected.

No route-level rate limits were found for these endpoints. Idempotency is not guaranteed for body measurements or sleep import: body measurements always append; sleep import overwrites by date.

## 6. Data Model & Persistence

Runtime files live under `DATA_DIR`, defaulting to the app directory when `DATA_DIR` is unset. This feature directly uses `data_body.json`, `data_sleep.json`, `data_workouts.json`, `data_soreness.json`, `data_cardio.json`, `data_recovery.json`, `data_settings.json`, `oura_daily.sqlite3`, `whoop.sqlite3`, and Open Wearables best-effort data.

`BODY_DATA` is an in-memory list loaded from `data_body.json`. Rows are dictionaries with at least date, weight, optional body-fat, optional tape fields, notes, and `created_at`. The add route appends and saves with `save_json`. There is no edit/delete/dedupe route in this feature.

`SLEEP_DATA` is an in-memory list loaded from `data_sleep.json`. Import merges by date into a dictionary, sorted ascending by date, and writes the entire JSON file while holding `JSON_DATA_LOCK`. The import schema is loose and does not enforce physiologic bounds.

Workout analytics read `WORKOUTS` from `data_workouts.json`. Legacy workouts are backfilled with stable IDs at import time. Apple Health-derived workouts can be folded into analytics through helper functions, especially for ACWR, muscle volume, and cardio fatigue. `history_normalization.py` provides canonical categories and source labels for History/Stats presentation, especially `strength_training`, `Strength - Logged`, and `Strength - Watch`.

Oura sleep analytics reads `oura_sleep` rows from `OURA_DB_FILE`; advanced analytics reads `oura_daily` sleep duration and HRV trend helpers. Vitals source precedence is Open Wearables first, then Oura daily cache, then WHOOP for missing heart-rate/sleep score fields.

The Markdown export is generated on demand from current in-memory `WORKOUTS`; it is not persisted unless the user downloads it.

## 7. Enums & Constants

Body trend: `increasing` means linear slope over recent weights is greater than `0.1`; `decreasing` means less than `-0.1`; `stable` means between those thresholds; `unknown` means fewer than three usable recent weights or zero regression denominator.

Navy body-fat output clamp: minimum `3.0`, maximum `60.0`.

Sleep analytics consistency: requires at least four parseable `sleep_start` times. Score is `100 - (population_stddev_bedtime_minutes / 90 * 100)`, clamped `0` to `100`, rounded to one decimal.

Sleep debt constants: target sleep is `420` minutes per night. Status bands are `good` for debt under 60 minutes, `mild` for 60-180, `moderate` for 181-300, and `severe` above 300 over the selected window.

ACWR windows: acute load is the last 7 calendar days inclusive. Chronic load is the average weekly load over up to the last 28 observed days. Risk bands are `detraining` when `<0.8`, `optimal` when `0.8` through `1.3`, `caution` when `>1.3` through `1.5`, and `high` when `>1.5`.

Muscle groups returned by `/api/muscle-fatigue`: `chest`, `back`, `shoulders`, `biceps`, `triceps`, `quads`, `hamstrings`, `glutes`, `adductors`, `calves`, `core`.

Muscle fatigue levels: readiness `>=8` is `recovered` with color `#10b981`; `>=6` is `mild` with `#84cc16`; `>=4` is `moderate` with `#f59e0b`; `>=2` is `high` with `#f97316`; below `2` is `severe` with `#ef4444`.

Volume status from `calculate_volume`: `<6` sets is `Below MEV`/red; `6-10` is `Minimum Effective`/yellow; `11-15` is `Optimal`/green; `16-20` is `High Volume`/yellow; `>20` is `Above MRV`/red.

Advanced volume landmarks default: `mv=6`, `mev=9`, `mav_min=12`, `mav_max=18`, `mrv=22`. Advanced landmark zones are `below_mv`, `mv`, `mev_to_mav`, `mav_high`, and `mrv_risk`.

Advanced fatigue formula: base `22` plus HRV penalty (`up:0`, `stable:5`, `down:12`, unknown default `6`), sleep penalty up to `20` (`debt_minutes / 30`), volume penalty up to `25` (`last 12 workout volume / 12000`), mean decayed soreness across chest/back/quads/hamstrings times `2`, autoregulation penalty `8` when average RPE of the last three sessions is over `8.5`, and mesocycle penalty up to `15` (`weeks_since_deload * 2.5`). Fatigue is capped at `100`. In `/api/analytics/advanced`, HRV-trend wiring is currently non-functional: the route passes the Oura DB path to a helper that expects HRV values and then expects `up`/`down` labels while the helper returns `improving`/`declining`, so HRV is always `unknown` with a constant +6 penalty.

Advanced recovery suggested intensity: `recovery` if fatigue `>80`, `light` if `>68`, `moderate` if `>52`, else `hard`.

Settings defaults used here: target weight `175`, target body fat `18`, fatigue threshold `72`, sessions per week `3`, available time `75`, equipment preference `machines_only`.

Progressive overload fixed exercises: `Chest Press`, `Lat Pulldown`, `Mid Row`, `Leg Press`, `Leg Curl`, `Seated Dip`, `Shoulder Press`, `Biceps Curl`. Change directions are `up`, `down`, or `flat`.

Insights types emitted by the backend: `positive`, `warning`, `negative`, and `info`. Frontend icon mapping currently recognizes `success`, `warning`, `danger`, and `info`, so `positive` and `negative` fall through to the info styling [TBC if intentional].

Weather cache TTL: 600 seconds. Default location: `San_Antonio`. Provider: `wttr.in` JSON endpoint with `User-Agent: fitness-dashboard/1.0` and 10-second timeout.

## 8. Integration Points

Body recomposition reads settings targets and feeds the Body tab target-progress card. Nutrition trend and interpretation cards are adjacent but owned by nutrition PRDs; this feature consumes the resulting body context rather than owning meal estimation.

Vitals integrates Open Wearables, Oura daily cache, and WHOOP. Open Wearables is preferred for current heart-rate/activity/sleep event data. Oura fills missing heart-rate, steps, active calories, sleep duration, sleep stage, and sleep-score fields. WHOOP fills missing resting HR and sleep quality when its latest fact is fresh or aging.

ACWR and muscle fatigue integrate Apple Health workouts via recommendation/workout helper functions, so watch-recorded cardio and strength sessions can affect load and readiness. Tests explicitly cover Apple Health walk/strength contribution and duplicate app-vs-watch cardio handling.

Advanced analytics integrates sleep debt, soreness history, workout RPE, and volume. Oura HRV trend is currently non-functional in this route, and deload detection reduces to `fatigue_score >= fatigue_threshold` because the detector result key is mismatched (`recommended` vs `needed`).

Weather integrates with Settings, Dashboard, and smart recommendation. Settings fetches live weather for explicit query, last cached location, then `San_Antonio`. Dashboard and smart recommendation consume only warm cache to avoid blocking.

Markdown export integrates only workout history. It does not include body, sleep, vitals, nutrition, adherence, or recovery analytics.

## 9. Permissions & Security

All routes in this PRD are owner-session protected by the global auth guard. Public exceptions are limited elsewhere to login/register/static and Apple Health sync. API callers without a session receive `401`; non-owner sessions receive `403`.

Mutating routes in this PRD (`/api/add-body-measurement`, `/api/body/navy-calc`, `/api/sleep/import`) are protected by the global CSRF/same-origin guard. The frontend API helper always sends `X-Requested-With: XMLHttpRequest` and `credentials: same-origin`.

Responses carry no-store/no-cache headers from the app-level response hook. The app also sets permissive CORS headers globally (`Access-Control-Allow-Origin: *`), but the session auth and CSRF guard still protect these owner data APIs from unauthenticated reads or cross-site mutation.

Weather returns wttr.in current-condition data including a `raw.current_condition` subset. This is not private health data, but the requested `location` can disclose the user's configured/query location to the external provider when `/api/weather` is called.

## 10. Business Rules

The body feature treats weight as required for a saved measurement even though the UI toast says "Enter a value" if either weight or body-fat is present. A body-fat-only UI save will pass the client precheck but fail server validation because `weight_lbs` is required.

Body-history trend mutates the in-memory row objects by adding `weight_change` before returning. Because these objects are from `BODY_DATA`, subsequent saves or backup exports may include derived `weight_change` if the data is later written [TBC: observed mutation, no immediate write in the endpoint].

Recomp ETA uses the first weight from roughly the last 14 entries and divides the change by `2.0` to estimate weekly velocity. This assumes 14 entries roughly equals two weeks; it does not check actual date spacing.

Manual sleep analytics merges manual and Oura data per date. Oura wins on overlapping dates; manual-only dates remain visible, and manual rows without an explicit source use `manual`.

Sleep performance correlation compares a sleep row's duration to the next calendar day's average e1RM from logged workout sets. It requires at least three paired dates and ignores workouts without calculable set e1RM.

Muscle readiness combines recent soreness, recent training recovery debt, cardio fatigue, recent overall fatigue, and performance debt from missed planned reps/sets. Recent workout fatigue only looks back 48 hours; performance debt looks back 72 hours. Soreness uses only entries from the last 24 hours in readiness, while advanced analytics uses a separate 2-day half-life soreness decay.

Adherence counts only workouts with `recommendation_id` in the denominator. Unresolved recommendations can store `adherence.followed = None`; those count as linked completions but not followed completions.

Progression status uses e1RM (`weight * (1 + reps/30)`). Regression means current e1RM is below 95% of peak. Plateau means the latest of the last three e1RMs is less than or equal to the first of those three. Otherwise the exercise is `On Track`.

Progress insights suppress regression warning cards until there are at least six workouts in the last 30 days; below that, regressions create a "Ramping back up" info card.

Push/pull balance uses last four weeks, push muscles `chest`, `shoulders`, `triceps`, and pull muscles `back`, `biceps`. Ratio `0.8-1.2` is balanced/green; above `1.2` is push-heavy/yellow; below `0.8` is pull-heavy/yellow; above `1.5` or below `0.67` is red.

Injury risk flags volume spike when recent two-workout set count is more than 30% higher than the previous two-workout set count, high intensity when at least six sets in the last four workouts are RPE 9+, and persistent soreness when a muscle has at least two soreness entries at level 6+.

## 11. Config & Environment

`DATA_DIR` controls where JSON and SQLite runtime files live. If unset, data files are read/written beside the app source.

`SECRET_KEY` env var overrides; otherwise the app reads or auto-generates and persists `.flask-secret` in the project dir. Startup refuses only the empty/known-default secret. `SESSION_COOKIE_SECURE` defaults to true unless explicitly set to `false`.

`FITNESS_DASHBOARD_PUBLIC_BASE_URL` participates in same-origin CSRF expectations for proxied deployments.

`DEBUG_TIMING=1` enables timing logs for selected endpoints; none of the PRD-owned endpoints except shared dashboard consumers are in the debug set.

Weather has no environment-backed provider key. It uses the public wttr.in endpoint. Default location is hardcoded to `San_Antonio`, with an optional `location` query parameter and in-memory cache.

Advanced analytics reads settings values from `data_settings.json`, especially `target_weight_lbs`, `target_body_fat_pct`, `fatigue_threshold`, and `volume_landmarks.default`. Settings validation applies when values are changed through `/api/settings`; direct file edits or backup imports can still affect shape [TBC].

## 12. Test Coverage

`tests/test_stats_insights_empty_history.py` covers cold-open behavior, requiring no sample workouts unless `FIT_LOAD_SAMPLE_DATA=1`, no seeded progress insights on empty history, and no progress cards for one-off exercises without repeated history.

`tests/test_progress_loop_completion_to_recommendation.py` covers adherence resolution against the live recommendation cache, unresolved recommendation handling, persisted `WORKOUTS` as the adherence source, denominator behavior, and expected `adherence_rate`.

`tests/test_muscle_recovery_performance_debt.py` covers muscle fatigue/readiness effects from recent training debt, overall fatigue, soreness, missed reps, missed sets, incomplete set rows, planned target persistence, and recommendation fallback behavior.

`tests/test_apple_health_recommendation_bridge.py` covers Apple Health contribution to ACWR, cardio fatigue, strength volume/readiness, duplicate app-vs-Apple walk handling, duration normalization, and optional HR-intensity load raising.

`tests/test_vitals_hr_zone_guarded_on_missing_data.py` covers the Vitals UI contract that missing HR data must not render an invented HR-zone subtitle.

`tests/test_fit152_linechart_nonnegative_y.py` covers a shared charting contract for non-negative Y axes. It targets History volume charts, not body or stats directly, but protects progress chart rendering behavior.

`tests/test_freshness.py` and `tests/test_dynamic_cardio_recommendations.py` cover weather cache-only behavior for dashboard/smart recommendation consumers.

Coverage gaps: no focused tests were found for `/api/add-body-measurement`, `/api/body-history`, `/api/body-recomp`, `/api/body/navy-calc`, `/api/sleep/import`, `/api/sleep/analytics`, `/api/progressive-overload`, `/api/analytics/advanced`, or `/api/export-md`.

## 13. Gaps & Issue Candidates

### IC-1: Align body-fat-only saves with API validation
- **Type:** Bug
- **Priority:** medium
- **Where:** `static/js/app.js:6959`, `app.py:8550`
- **Problem:** The Body tab allows a save attempt when either weight or body-fat percent is present, but `/api/add-body-measurement` requires `weight_lbs`. A user entering only body fat gets a generic save failure instead of a clear validation rule.
- **Why it matters:** The app invites an action the backend rejects, creating avoidable friction in the body log.
- **Acceptance criteria:**
  - Client and server agree whether body-fat-only entries are allowed.
  - If weight remains required, the UI blocks body-fat-only saves with specific copy.
  - If body-fat-only is allowed, the API accepts `weight_lbs: null` and charts handle it.
- **Duplicate-of:** none

### IC-2: Validate body measurement date and tape fields
- **Type:** Data-contract
- **Priority:** medium
- **Where:** `app.py:8550`
- **Problem:** The body add route validates weight, body-fat, and notes, but stores `date`, `neck_in`, `waist_in`, `chest_in`, `hips_in`, `arms`, and `legs` as passthrough values. Bad strings or impossible values can later appear in charts, backups, or body recomposition payloads.
- **Why it matters:** Progress analytics are only trustworthy if measurement data has stable units and valid ranges.
- **Acceptance criteria:**
  - `date` accepts only `YYYY-MM-DD` or a documented ISO date contract.
  - Tape fields are numeric inches with explicit min/max ranges or explicitly unsupported.
  - Invalid fields return structured `invalid_field` errors.
  - Existing legacy rows continue to render safely.
- **Duplicate-of:** none

### IC-3: Productize the Navy calculator and tape-measure flow
- **Type:** Feature
- **Priority:** low
- **Where:** `app.py:15757`, `templates/index.html:580`
- **Problem:** `/api/body/navy-calc` implements a real body-fat calculator, but the app-shell Body tab exposes only weight and body-fat percent fields. The composition area can display measurements, but the user cannot enter the tape fields or invoke the calculator from the UI.
- **Why it matters:** A useful body-composition helper exists but is effectively hidden from the owner workflow.
- **Acceptance criteria:**
  - Body tab includes a compact tape-measure entry/calculator flow or the endpoint is documented as API-only.
  - Calculated `body_fat_pct` can be reviewed before save.
  - Male/female formula inputs and validation errors are visible.
  - Saved rows preserve tape fields with validated units.
- **Duplicate-of:** none

### IC-4: Preserve manual sleep visibility when Oura rows exist
- **Type:** Data-contract
- **Priority:** medium
- **Where:** `app.py:15814`
- **Problem:** `/api/sleep/analytics` reads Oura long-sleep rows first and falls back to manual `SLEEP_DATA` only when Oura returns no rows. Manual imports are completely hidden when any Oura sleep history exists, even for dates Oura is missing.
- **Why it matters:** The owner can import local sleep data and still never see it in analytics, making the import path feel broken.
- **Acceptance criteria:**
  - Sleep analytics defines source precedence per date, not all-or-nothing by table.
  - Response includes source provenance for every sleep row.
  - Manual-only dates remain visible when Oura has other dates.
  - Tests cover Oura/manual overlap and gap-fill behavior.
- **Duplicate-of:** none

### IC-5: Harden sleep import against impossible sleep rows
- **Type:** Bug
- **Priority:** high
- **Where:** `app.py:15778`
- **Problem:** Manual sleep import converts numeric fields with `int(float(...))` but does not reject negative, impossible, or contradictory durations. It can also raise unhandled conversion errors for malformed numeric strings.
- **Why it matters:** Bad sleep rows can pollute analytics or trigger generic 500-style failures instead of actionable import errors.
- **Acceptance criteria:**
  - Sleep duration, in-bed, stages, and awake minutes have explicit non-negative bounds.
  - Stage totals are checked against total sleep/in-bed where feasible.
  - Malformed rows return structured row-level errors or a documented skip policy.
  - Existing tests cover invalid, partial, and valid CSV imports.
- **Duplicate-of:** none (related: FIT-234)

### IC-6: Fix bedtime consistency for midnight wraparound
- **Type:** Bug
- **Priority:** medium
- **Where:** `app.py:15853`
- **Problem:** Bedtime consistency converts sleep-start timestamps to minutes since midnight and uses plain population standard deviation. Bedtimes around midnight, such as 23:50 and 00:10, can appear far apart even though they are close in real behavior.
- **Why it matters:** The consistency score can penalize normal late-night timing and mislead recovery interpretation.
- **Acceptance criteria:**
  - Bedtime variance uses circular time or a documented anchor window.
  - Overnight edge cases around midnight have tests.
  - Response copy/field name reflects what is actually measured.
- **Duplicate-of:** none

### IC-7: Align progress insight type names with frontend styling
- **Type:** Bug
- **Priority:** low
- **Where:** `app.py:15411`, `static/js/app.js:5285`
- **Problem:** The backend emits `positive` and `negative` insight types, while the frontend maps `success`, `warning`, `danger`, and `info`. Positive and negative insights therefore fall through to info styling/icons.
- **Why it matters:** Important progress and risk cards lose visual priority.
- **Acceptance criteria:**
  - Frontend maps `positive` to positive styling and `negative` to negative styling, or backend emits frontend-supported names.
  - Tests cover all emitted insight types.
  - Existing empty-state behavior remains unchanged.
- **Duplicate-of:** none

### IC-8: Add focused tests for body and sleep endpoints
- **Type:** Test
- **Priority:** high
- **Where:** `tests/`, `app.py:8549`, `app.py:15778`
- **Problem:** No focused tests were found for body measurement logging/history/recomp, Navy calculator, manual sleep import, or sleep analytics. These endpoints handle personal progress data and contain several validation/precedence rules.
- **Why it matters:** Regressions in measurement and sleep data can silently corrupt trend interpretation.
- **Acceptance criteria:**
  - Tests cover body add validation, sorting, trend labels, rolling averages, and ETA edge cases.
  - Tests cover Navy male/female formula validation and output clamping.
  - Tests cover sleep import JSON/CSV, replacement by date, invalid rows, and Oura/manual precedence.
  - Tests run without private runtime data.
- **Duplicate-of:** none (related: FIT-264)

### IC-9: Make progressive overload either visible or explicitly API-only
- **Type:** Improvement
- **Priority:** low
- **Where:** `app.py:14560`, `static/js/app.js`
- **Problem:** `/api/progressive-overload` returns fixed-exercise top-set trends, but no current app-shell consumer was found. It also ignores exercises outside the hardcoded eight-machine list.
- **Why it matters:** A progress endpoint that is not visible and only covers part of the exercise library can misrepresent strength progression.
- **Acceptance criteria:**
  - Product decision recorded: visible Stats/Body card or API-only.
  - If visible, UI explains covered exercises and empty states.
  - Endpoint either derives exercises from library/settings or documents the fixed list.
  - Tests cover included, excluded, and empty-history cases.
- **Duplicate-of:** none

### IC-10: Harden Markdown export against partial workout rows
- **Type:** Bug
- **Priority:** medium
- **Where:** `app.py:15367`
- **Problem:** `/api/export-md` indexes `exercise["machine"]`, `s["weight_lbs"]`, and `s["reps"]` directly. Legacy, Apple Health-derived, or partially synced rows can lack those keys and cause export failure.
- **Why it matters:** Export should be a dependable escape hatch for local-first data, not fail on one imperfect row.
- **Acceptance criteria:**
  - Export uses safe getters and marks missing values as blank or `N/A`.
  - Export skips or labels non-strength/watch-only rows according to a documented rule.
  - Tests cover complete, partial, and empty workout histories.
- **Duplicate-of:** none

### IC-11: Add contract tests for advanced analytics thresholds
- **Type:** Test
- **Priority:** medium
- **Where:** `app.py:15886`
- **Problem:** `/api/analytics/advanced` combines volume landmarks, HRV trend, sleep debt, soreness decay, RPE, deload detection, and settings thresholds, but no focused tests were found for the formula or zone boundaries.
- **Why it matters:** This endpoint can recommend deload/light/recovery states; threshold drift would affect training decisions.
- **Acceptance criteria:**
  - Tests cover default volume landmark zones at boundary values.
  - Tests cover fatigue score factors and cap behavior.
  - Tests cover `fatigue_threshold` and `deload_recommended`.
  - Malformed or missing settings fall back predictably.
- **Duplicate-of:** none

### IC-11a: Fix advanced analytics HRV and deload detector wiring
- **Type:** Bug
- **Priority:** high
- **Where:** `app.py:15898`; `app.py:15918`; `oura_client.py:412`; `app.py:4131`
- **Problem:** `/api/analytics/advanced` always treats HRV trend as `unknown` because it calls `compute_hrv_trend` with the Oura DB path and expects labels the helper does not return; deload detector output is also ignored because the route reads `recommended` while the helper returns `needed`.
- **Why it matters:** Fatigue score always receives the flat HRV penalty and `deload_recommended` is driven only by the fatigue threshold.
- **Acceptance criteria:**
  - Advanced analytics passes real HRV values and maps helper labels correctly.
  - Deload detector reads the actual `needed` key.
  - Tests cover HRV trend labels, unknown fallback, and deload detector contribution.
- **Duplicate-of:** none

### IC-12: Remove raw provider payload from weather response
- **Type:** Privacy
- **Priority:** low
- **Where:** `app.py:10126`
- **Problem:** `/api/weather` returns `raw.current_condition` from wttr.in. This is not health data, but it expands the public response contract with provider-specific fields the UI does not need.
- **Why it matters:** Local-first APIs should expose the smallest useful payload, especially for contextual data fetched from external services.
- **Acceptance criteria:**
  - Weather response returns only documented normalized fields used by UI/recommendations.
  - If diagnostics need raw weather, they are behind an explicit debug flag or separate diagnostics route.
  - Tests confirm normalized cache and API responses stay stable.
- **Duplicate-of:** none
