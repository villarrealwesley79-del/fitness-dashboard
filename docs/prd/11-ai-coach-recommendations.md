# AI Coach / Recommendation Engine — PRD

> **Sources:** README.md; docs/VISION.md; docs/PRD.md; docs/CURRENT_STATE.md; app.py; ai_fact_query.py; workout_adaptation.py; whoop_recommendations.py; recommendation_sources.py; lm_studio_adapter.py; history_normalization.py; data_loader.py; tests/test_ai_fact_context.py; tests/test_ai_health_metrics.py; tests/test_fit136_workout_adaptation.py; tests/test_dynamic_cardio_recommendations.py; tests/test_apple_health_recommendation_bridge.py
> **Routes:** GET /api/recommendation/smart; GET /api/next-workout; GET /api/dashboard; GET /api/workout-adaptation-events; POST /api/workout-adaptation-events/{event_id}/ack; POST /api/workout/adjust; POST /api/workout/analyze; GET /api/ai/health; GET /api/ai/metrics; GET /api/wearable-sources; GET /api/wearable-facts; GET /api/ai/facts/context; POST /api/ai/facts/query; POST /api/ai/suggestions/{suggestion_id}/approve; POST /api/ai/suggestions/{suggestion_id}/reject; GET /api/whoop/recommendation-signals
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

## 1. Overview

The AI Coach / Recommendation Engine turns the owner's local training history, readiness, soreness, nutrition, weather, and wearable signals into a conservative daily training recommendation. It is not a free-form AI planner. Deterministic Python logic remains the source of truth for training decisions; the LLM is used only to translate plain-language constraints into a validated intent patch and to produce post-workout analysis text.

The primary scenario is: the owner opens the dashboard or workout tab, sees today's recommendation, understands why it is intensity, moderate, or recovery, and can trust that stale or incomplete wearable data is labeled honestly. If food is accepted during the day, the engine waits for a short coalescing window, evaluates only accepted food entries, and may reduce remaining volume or shift the next day toward recovery when conservative nutrition rules are met.

The engine also exposes a sanitized fact-query context for AI coach questions. That context contains freshness, wearable source metadata, public wearable facts, and normalized workout history. It intentionally excludes raw provider payloads and secrets. Suggested AI actions require explicit approval, and approval currently records status only; it does not mutate training or food data.

Daily-brief rendering is owned by [02 Daily Brief Dashboard](02-daily-brief-dashboard.md). Workout execution UX is owned by [03 Workout Planning Execution](03-workout-planning-execution.md). This PRD owns the recommendation semantics, source hierarchy, AI fact context, and engine contracts.

## 2. User-Facing Surfaces

The recommendation engine feeds these surfaces:

| Surface | Region | Behavior |
| --- | --- | --- |
| Dashboard daily command brief | Main card | Displays the current training recommendation, freshness/confidence, nutrition context, source proof, and next workout summary. Rendering details are out of scope here. |
| Workout tab recommendation | Active plan area | Shows the deterministic next workout, after applying safe wearable and food-adaptation modifiers. The user can adjust with natural language or start the workout. |
| AI Coach adjust modal | Natural-language constraint input | Accepts a short request such as "avoid shoulder" or "only 45 minutes"; returns a safe patched plan, refusal, or fallback. |
| Workout analysis modal | Post-workout analysis | Runs a read-only analysis of a logged workout using recent same-muscle history and session notes. |
| Wearable source proof chips | Dashboard/settings support region | Lists Open Wearables, WHOOP, Oura, and Apple Health freshness and recommendation influence. |
| Workout adaptation event feed | Toast/banner/event list consumer | Provides applied/no-change nutrition adaptation events and an acknowledgement action. |
| AI fact query UI/API consumer | Coach question surface | Lets the owner ask questions against sanitized facts and history; suggested actions require approval. |
| AI health/metrics panel | Settings or debug surface | Shows LM Studio reachability, active primary/fallback model, and recent adjustment reliability metrics. |

Conditional visibility and confidence behavior:

| Condition | Surface effect |
| --- | --- |
| Open Wearables has fresh or aging facts and conservative modifier applies | Recommendation is held conservative once and source proof marks Open Wearables as used for recommendation. |
| WHOOP has no fresh scored fact | WHOOP is display-only, explanations say it is context-only, and it does not modify the plan. |
| Apple Health is stale/missing while Open Wearables is used and the load source is Apple Health | `load_source_summary_hidden=true` hides stale legacy load-source summary copy. |
| Active workout is open but completed sets are not supplied | Food-adaptation evaluation is skipped to avoid silently changing an in-progress plan. |
| Active workout is open and completed sets are supplied | Adaptation may update live, but completed sets are preserved. |
| LM Studio unavailable and deterministic swap can satisfy the constraint | Adjust returns `status=ok` with deterministic fallback notes, not a broken AI state. |
| LM Studio unavailable and no deterministic swap applies | Adjust returns `status=fallback` with the unchanged recommendation. |

## 3. Field Inventory

### Smart recommendation response

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| recommendation | enum string | Yes | moderate | intensity, moderate, recovery | Final daily training stance after readiness, fatigue, weather, WHOOP, and Open Wearables modifiers. |
| readiness | number/null | No | null | Oura readiness score if present | Raw Oura readiness for today. |
| effective_readiness | number/null | No | null | Rounded to 1 decimal | Readiness after recent recovery bonus. |
| sleep_score | number/null | No | null | Oura value | Sleep score context. |
| hrv | number/null | No | null | Oura value | Current HRV context. |
| hrv_trend | enum string | Yes | unknown | Values from `compute_hrv_trend` [TBC: exact enum defined outside assignment sources] | Seven-day HRV direction. |
| readiness_factors.acwr | object | Yes | {} | Deterministic ACWR output | Acute/chronic workload risk context. |
| readiness_factors.sleep_debt | object | Yes | {} | Deterministic sleep-debt output | Seven-day sleep debt and status. |
| readiness_factors.recovery_bonus | object | Yes | {} | Deterministic recovery-bonus output | Recovery activity credit. |
| avoid_muscles | string[] | Yes | [] | Soreness >= 6 or recent trained muscles | Muscles to avoid today. |
| recently_trained | object[] | Yes | [] | Last completed workout < 18h; >=2 sets per muscle | Muscle groups protected due to recent loading. |
| last_completed | object/null | No | null | Last completion within 24h when found | Recently completed workout summary. |
| suggested_workout | string | Yes | Normal training... | Derived text | High-level session focus suggestion. |
| weather | object/null | No | null | Cached wttr.in response | Heat/cold context for conservative downgrades. |
| time_of_day | enum string | Yes | Derived | morning, afternoon, evening | Time context included in reasoning. |
| history_context | string[] | Yes | [] | Last four muscles with days-ago | Lightweight training history context. |
| reasoning | string | Yes | No Oura/soreness data available | Semicolon-delimited evidence | Human-readable explanation of the recommendation. |
| freshness | object | Yes | computed | See freshness fields below | Source freshness and food target state. |
| wearable_sources | object[] | Yes | [] | See wearable source fields | Source proof for the recommendation. |
| recommendation_sources | object | Yes | {} | See source hierarchy | Detailed influence and conflict proof. |
| nutrition_context | object | Yes | computed | See nutrition fields below | Accepted food and macro context. |
| next_workout | object | Yes | generated plan | Auth-scoped | Actual workout plan after modifiers/adaptations. |
| workout_adaptation_events | object[] | Yes | [] | Public FIT-137 event projection | Nutrition adaptation events generated during the request. |
| confidence_level | enum string | Yes | low | low, medium, high | Overall confidence derived from readiness and data freshness. |

### Freshness block

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| open_wearables.status | enum | Yes | missing | fresh, missing, stale | Hub freshness bucket; connected maps to fresh, blocked/error maps to stale. |
| open_wearables.hub_status | enum/null | No | null | connected, blocked, error, missing_config, etc. [TBC: full hub enum defined in Open Wearables PRD] | Underlying hub state. |
| open_wearables.last_sync_attempt | ISO string/null | No | null | Last checked timestamp | Last hub status check. |
| oura.status | enum | Yes | missing | missing, fresh, aging, stale | Oura data-point freshness. |
| oura.last_data_point | date/null | No | null | Oura daily row date | Most recent Oura day, not merely sync time. |
| oura.last_sync_attempt | ISO string/null | No | null | Oura row created_at | Last Oura cache upsert. |
| oura.source | enum | Yes | cached | live, cached | Live if last sync attempt is under 1 hour old; otherwise cached. |
| whoop.status | enum | Yes | missing | missing, fresh, aging, stale | WHOOP daily fact freshness. |
| whoop.score_state | enum/null | No | null | SCORED or provider state | Whether WHOOP fact is safe to modify with. |
| whoop.connected | boolean | Yes | false | Connection status | Whether OAuth/local status is connected. |
| apple_health.status | enum | Yes | missing | missing, fresh, aging, stale | Apple Health sync log freshness. |
| apple_health.last_data_point | date/null | No | null | MAX(record_date) | Most recent Health Auto Export record date. |
| food.status | enum | Yes | missing | missing, fresh, aging, stale | Latest accepted food date freshness. |
| food.pending_review | boolean | Yes | false | Pending estimate check | Whether today's food has pending estimates excluded from plan changes. |
| food.target_state | enum | Yes | none | none, under, on_track, over | Today's calories against target. |
| food.calories_pct | integer | Yes | 0 | Derived from accepted entries | Calorie target progress. |
| food.protein_pct | integer | Yes | 0 | Derived from accepted entries | Protein target progress. |

Freshness buckets use data-point age: fresh under 24 hours, aging from 24 to under 48 hours, stale at 48 hours or more, and missing when no data point exists.

### Nutrition context and adaptation contract

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| date | YYYY-MM-DD | Yes | Today | Browser-local date parsing where supplied | Nutrition day being evaluated. |
| totals.calories | integer | Yes | 0 | Accepted food logs preferred; legacy nutrition fallback | Accepted calories logged. |
| totals.protein_g/carbs_g/fat_g/fiber_g | number | Yes | 0 | Accepted food logs preferred | Accepted macros. |
| totals.sodium_mg | integer | Yes | 0 | Accepted food logs preferred | Sodium used for next-day recovery context. |
| targets.calories | integer | Yes | 2200 | Settings `daily_calorie_target` or default | Daily calorie target. |
| targets.protein_g | number | Yes | 148 or weight*0.8 | Settings or latest body weight | Daily protein target. |
| remaining.* | number | Yes | target minus totals | Can go negative for calories | Remaining macros. |
| percentages.calories | integer | Yes | 0 | Rounded target progress | Fueling progress. |
| percentages.protein | integer | Yes | 0 | Rounded target progress | Protein progress. |
| accepted_entries_count | integer | Yes | 0 | Only accepted logs | Count of entries allowed to influence plan. |
| pending_review_count | integer | Yes | 0 | Pending/review states | Count excluded from plan decisions. |
| warnings[] | object[] | Yes | [] | See warning enums | Non-mutating nutrition warnings. |
| next_day_context | object | Yes | {} | keys `high_sodium`, `late_meal`, `late_entries_count`, `notes[]` | Recovery context for tomorrow. |
| plan_adjustment.allowed | boolean | Yes | true | Constant | Food can adjust plans only via accepted food and event feed. |
| plan_adjustment.mode | enum | Yes | accepted_food_only | Constant | Pending estimates cannot change workouts. |
| plan_adjustment.delivery | enum | Yes | pollable_event_feed | Constant | Adaptations are consumed via polling. |
| plan_adjustment.coalescing_window_seconds | integer | Yes | 180 | Constant | Delay after food acceptance before evaluation. |

### Workout adaptation event

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| id | UUID string | Yes | Generated | Max 80 for ack route | Event identity. |
| status | enum | Yes | no_change | applied, no_change | Whether a plan change was made. |
| silent | boolean | Yes | true for no_change | Boolean | Whether UI can hide the event. |
| change_type | enum | Yes | none | none, reduce_volume, rest_recovery, remove_strength_sets | Type of workout change. |
| date | YYYY-MM-DD | Yes | Pending window date | Date string | Food/adaptation day. |
| applies_to | enum | Yes | today | today, next_day, expired | Whether change affects same-day, next-day, or a stale window that records silent no-change. |
| reason | string | Yes | Rule text | Neutral-language guard rejects moral labels | User-facing explanation. |
| confidence.level | enum | Yes | low | low, medium, high | Confidence from accepted food estimate confidence. |
| confidence.score | number | Yes | 0 | Minimum confidence among trigger logs | Numerical confidence. |
| confidence.no_change_reason | enum/null | No | null | low_confidence, no_science_supported_change, stale_window | Why no change happened. |
| trigger.meal_ids | string[] | Yes | [] | Pending window meal ids | Food records that triggered evaluation. |
| trigger.food_log_client_ids | string[] | Yes | [] | Pending window client ids | Food log client ids. |
| patch.operations | object[] | Yes | [] | reduce_sets, set_recovery_note, cap_exceeded, cardio_duration reductions [TBC: exact cardio op name from clamp helper] | Concrete changes. |
| active_workout.was_open | boolean | Yes | false | Query-param driven | Whether an active workout was protected. |
| active_workout.updated_live | boolean | Yes | false | Applied and active | Whether in-progress plan changed. |
| active_workout.preserve_completed_work | boolean | Yes | true | Constant | Completed sets must not be removed. |
| reason_metadata.rules | string[] | Yes | [] | Rule ids | Auditable decision proof. |
| reason_metadata.citations | object[] | Yes | [] | Science citations | Evidence basis for rules. |
| acknowledged_at | ISO string/null | No | null | Set by ack route | UI dismissal state. |
| created_at | ISO string | Yes | Evaluation time | Server time | Event creation. |

### AI fact context and answer

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| generated_at | ISO string | Yes | now | Server time | Context build time. |
| freshness | object | Yes | {} | Same freshness block | Source freshness proof. |
| wearable_sources | object[] | Yes | [] | Sanitized source metadata | Public provider status. |
| facts | object[] | Yes | [] | Public wearable facts only | Sanitized wearable metrics. |
| history | object[] | Yes | [] | Max 80 normalized rows | Local and Apple Health workout history. |
| mutation_policy | enum | Yes | suggestions_require_approval | Constant | AI cannot mutate without approval. |
| answer | string | Yes | Generated | Deterministic | Human-readable response. |
| evidence | object[] | Yes | [] | source/date_range/freshness | Proof used in the answer. |
| uncertainty | string/null | No | null | Present when evidence missing | Why answer is limited. |
| suggested_action_id | string/null | No | null | `ai-suggestion-<12 hex>` | Pending suggestion id when requested and evidence exists. |
| suggestion | object | No | null | `id`, `kind:"review"`, `summary`, `evidence`, `status:"pending"`, `created_at` | Full pending suggestion object returned alongside `suggested_action_id` when `suggest=true` and evidence exists. |

### AI adjust request/response

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| constraint | string | Yes | none | Required, max 280 chars | Owner's natural-language adjustment request. |
| status | enum | Yes | ok/fallback | ok, fallback | Whether AI/deterministic path returned a usable result. |
| result_kind | enum | No | changed | changed, refused, unchanged | Whether a safe patch changed the plan. |
| recommendation | object | Yes | Current deterministic plan | Auth scoped | Patched or unchanged workout. |
| summary | string/null | No | null | LLM or deterministic text | Short explanation. |
| applied_notes | string[] | Yes | [] | Safety-applied notes | Specific plan changes. |
| skipped_notes | string[] | Yes | [] | Safety-rejected no-ops | Requested changes that were not applied. |
| cache_hit | boolean | No | false | Cache lookup result | Whether cached response was reused. |
| meta | object | No | {} | Model metadata without prompt text | Model/version/latency info. |

LLM intent fields are strictly typed: `avoid_muscles[]`, `avoid_joints[]` with side `left|right|both` and joint `shoulder|elbow|wrist|hip|knee|ankle|spine`, `swap[]`, `rpe_delta`, `sets_delta_pct`, `duration_cap_min`, and `drop_cardio`.

### AI health and metrics

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| reachable | boolean | Yes | false | LM Studio probe | Whether any candidate endpoint responds. |
| url | string | Yes | primary URL | Config/env | Active candidate URL. |
| model | string | Yes | primary model | Config/env | Active model. |
| model_loaded | boolean | Yes | false | `/v1/models` target check | Whether expected model is loaded. |
| active_role | enum/null | No | null | primary, fallback | Which candidate is active. |
| fallback_active | boolean | Yes | false | Role check | Whether fallback is serving. |
| window_hours | integer | Metrics | 24 | Query clamped 1-720 | Metrics window. |
| adjust_requests | integer | Metrics | 0 | Count | Total adjust attempts. |
| ok/cache_hits/fallbacks | integer | Metrics | 0 | Count | Outcome buckets. |
| fallback_pct/cache_hit_pct | number | Metrics | 0 | Rounded percent | Reliability indicators. |
| avg_latency_ms | integer | Metrics | 0 | OK rows only | Average generation latency. |
| recent[] | object[] | Metrics | [] | Last 5 | Timestamp/outcome/latency/reason; prompt/completion omitted. |

## 4. Interactions & Flows

**Load smart recommendation.** Trigger: dashboard or client calls `GET /api/recommendation/smart`. Behavior: read Oura readiness, seven-day HRV trend, 24-hour soreness, last completed workout, ACWR, sleep debt, recovery bonus, weather, WHOOP context, Open Wearables facts, nutrition context, and next workout. Validation: best-effort reads fail closed into missing data rather than 500. API: `GET /api/recommendation/smart`. Success: returns final recommendation, source proof, nutrition context, next workout, and any adaptation events. Failure: unhandled server errors return Flask error [TBC: no route-specific error wrapper observed].

**Generate next workout.** Trigger: `GET /api/next-workout`, dashboard load, smart recommendation, or adjust fallback. Behavior: build a deterministic plan from training history, soreness, goal settings, available time, mesocycle week, readiness, equipment preference, and cardio rotation. Validation: cached plan is reused only when the recommendation fingerprint matches; hidden context generation must not consume cardio rotation. Success: returns the workout after food and wearable modifiers. Failure: best-effort wearable reads degrade to missing freshness.

**Apply wearable modifiers.** Trigger: smart recommendation, next-workout, and dashboard requests. Behavior: WHOOP can modify only when fresh/aging and `score_state=SCORED`; CSV-only WHOOP facts can feed signals without OAuth when fresh/scored. Open Wearables can downgrade based on public facts; `/api/next-workout` applies its conservative guard before plan generation, while the smart route applies it after the base recommendation. Apple Health and Oura are direct context/load sources rather than direct plan patchers in this layer. WHOOP modifiers stamp `_whoop_modifier_signature` so repeated modifier calls do not stack volume/RPE reductions. Success: source proof says which source was used and which modifier applied. Failure: stale or missing sources are display-only or missing.

**Coalesce accepted food into workout adaptation.** Trigger: accepted food logs enqueue a pending adaptation window. Behavior: after 180 seconds, normal workout or event-feed reads evaluate the closed window. Validation: only accepted food logs count; pending/review/needs_review entries are excluded; missing trigger rows do not consume the pending window. API: `GET /api/workout-adaptation-events`, `GET /api/next-workout`, or dashboard/smart routes can evaluate due windows. Success: event is persisted and returned. Failure: low confidence or unsupported signals create a silent `no_change` event.

**Protect active workouts.** Trigger: recommendation/event polling with query `active_workout_open=true`. Behavior: if no `completed_sets` payload is supplied, the route skips adaptation evaluation. If completed-set counts are supplied, the adaptation may reduce future sets but must preserve completed work. Success: event `active_workout` reports whether live update occurred. Failure: invalid `completed_sets` parsing behavior is [TBC: parser is outside sampled lines].

**Acknowledge adaptation event.** Trigger: user dismisses event. Validation: event id required and length <= 80. API: `POST /api/workout-adaptation-events/{event_id}/ack`. Success: sets `acknowledged_at` if event exists. Failure: 400 `invalid_field` for bad id, 404 `not_found` if missing.

**Ask AI fact question.** Trigger: user submits a coach question. Validation: `question` required. API: `POST /api/ai/facts/query`. Success: deterministic answer with evidence and uncertainty. If `suggest=true` and evidence exists, creates pending in-memory suggestion. Failure: 400 `missing_question`; no evidence returns answer with uncertainty and no suggestion.

**Approve or reject AI suggestion.** Trigger: user approves/rejects suggestion. Behavior: updates in-memory status and timestamp; `mutation_applied=false`. Success: returns suggestion. Failure: 404 `suggestion_not_found` after restart, worker change, or invalid id.

**Adjust workout with AI coach.** Trigger: user posts a constraint. Validation: constraint required, max 280 chars; LLM response must match strict JSON schema; deterministic safety rails clamp or reject unsafe intent. API: `POST /api/workout/adjust`. Success: changed/refused/unchanged result with patched recommendation. Failure/degraded: LM Studio unavailable, timeout, invalid JSON, or safety-rail error returns deterministic fallback or unchanged plan with `status=fallback`.

**Analyze workout.** Trigger: user requests analysis for a logged workout. Validation: body may contain `workout_id`, `workout_date`, or `latest=true`; missing target returns 404 `not_found`. API: `POST /api/workout/analyze`. Success: read-only summary, wins, concerns, comparison, next-session cue, and context-used proof. Failure/degraded: LM Studio errors return deterministic analysis fallback.

**AI health and metrics.** Trigger: settings/debug panel. Behavior: health probes primary and fallback LM Studio candidates; metrics aggregate local SQLite rows. Validation: metrics `hours` clamps to 1-720 and defaults to 24. Success: returns active endpoint/model and reliability buckets. Failure: adapter missing returns 200 reachable false; metrics DB failure returns 500 with error string.

## 5. API Endpoints

| Method | Path | Auth | Trigger | Key params | Response shape | Real/Mock |
| --- | --- | --- | --- | --- | --- | --- |
| GET | /api/recommendation/smart | Session auth | Dashboard/workout summary | `active_workout_open`, `completed_sets` | Smart recommendation object | Real |
| GET | /api/next-workout | Session auth | Workout tab/dashboard | `active_workout_open`, `completed_sets` | Next workout plus context | Real |
| GET | /api/dashboard | Session auth | App load | none/query context [TBC] | Dashboard aggregate | Real |
| GET | /api/workout-adaptation-events | Session auth | Polling | `unacknowledged`, `since`, `limit`, `active_workout_open`, `completed_sets` | `{events,count}` | Real |
| POST | /api/workout-adaptation-events/{event_id}/ack | Session auth + CSRF/same-origin | Dismiss event | event id | `{status,id}` | Real |
| POST | /api/workout/adjust | Session auth + CSRF/same-origin | AI coach adjust | JSON `constraint` | Adjust response | Real with deterministic fallback |
| POST | /api/workout/analyze | Session auth + CSRF/same-origin | Post-workout analysis | `workout_id`, `workout_date`, or `latest` | Analysis response | Real with deterministic fallback |
| GET | /api/ai/health | Session auth | Settings/debug | none | LM Studio health | Real; adapter may be absent |
| GET | /api/ai/metrics | Session auth | Settings/debug | `hours` | Metrics aggregate | Real local SQLite |
| GET | /api/wearable-sources | Session auth | Source proof | none | `{sources}` | Real sanitized metadata |
| GET | /api/wearable-facts | Session auth | AI/source proof | `limit` 1-100 default 30 | `{facts}` | Real sanitized facts |
| GET | /api/ai/facts/context | Session auth | AI fact UI | none | Sanitized fact context | Real |
| POST | /api/ai/facts/query | Session auth + CSRF/same-origin | AI question | JSON `question`, optional `suggest` | Fact answer | Deterministic, not LLM |
| POST | /api/ai/suggestions/{id}/approve | Session auth + CSRF/same-origin | Approve suggestion | suggestion id | `{suggestion}` | Real in-memory status only |
| POST | /api/ai/suggestions/{id}/reject | Session auth + CSRF/same-origin | Reject suggestion | suggestion id | `{suggestion}` | Real in-memory status only |
| GET | /api/whoop/recommendation-signals | Session auth | WHOOP source proof | none | status, connection, signals, conflict | Real |

Route-specific details:

- `GET /api/workout-adaptation-events` is not purely read-only today: it may evaluate due pending food windows before listing events. It clamps `limit` to 1-50 and rejects non-integer limits.
- `POST /api/workout/adjust` uses cache keys that include prompt/cache version, recommendation content, normalized constraint, readiness date, model version, and exercise library hash. Cache hits update the server-side canonical recommendation.
- `POST /api/workout/analyze` accepts an empty JSON body only because the route reads `request.get_json(force=True, silent=True) or {}`; it still requires one target selector.
- `GET /api/ai/health` can return a shell reload response for browser app-shell version mismatch before probing LM Studio.

## 6. Data Model & Persistence

The engine reads and writes the shared local data layer described in [14 Data Layer & Persistence](14-data-layer-persistence.md).

| Store | Used by this feature | Persistence behavior |
| --- | --- | --- |
| `data_workouts.json` | Training history, recent completion, next workout generation, AI history context | Loaded into global `WORKOUTS`; saves are atomic JSON replacements. |
| `data_soreness.json` | 24-hour soreness avoid list and readiness scoring | Loaded into global `SORENESS_DATA`. |
| `data_settings.json` | Training goal, sessions/week, available time, macro targets, equipment preference | Loaded into global `USER_SETTINGS`. |
| `data_recovery.json` | Recovery bonus | Loaded into global `RECOVERY_DATA`. |
| `data_cardio.json` | Cardio rotation and fatigue/readiness load | Loaded into global `CARDIO_DATA`. |
| `oura_daily.sqlite3` | Oura readiness, sleep, HRV, freshness | Real SQLite cache. |
| `apple_health_sync.db` | Apple Health workout/load/freshness | Real SQLite cache, path can be overridden by `APPLE_HEALTH_SYNC_DB`. |
| `fitness_data.db` | Food logs, adaptation pending/events, AI-adjacent food cache data | Real SQLite store. |
| `whoop.sqlite3` | WHOOP connection, records, daily facts, sync runs | Real SQLite store; token material stored outside DB. |
| `wearable_facts.sqlite3` | Public Open Wearables facts and source rows | Real SQLite store, profile-scoped. |
| `ai_coach_cache.sqlite3` | Adjust/analyze cache and metrics | Real SQLite cache under `DATA_DIR`. |
| In-memory globals | `LAST_WORKOUT_RECOMMENDATION`, recommendation fingerprint, `AI_PENDING_SUGGESTIONS`, weather cache | Not durable across restart or multi-worker processes. |

`workout_adaptation_pending` stores accepted food windows with `status`, `meal_ids_json`, `food_log_client_ids_json`, `window_started_at`, and `window_closes_at`. `workout_adaptation_events` stores the public event contract as JSON columns: confidence, trigger, nutrition context, patch, before/after plan, active workout, and reason metadata.

`ai_coach_cache.sqlite3` contains `adjust_cache(cache_key, created_at, response_json)` and `adjust_metrics(id, ts, outcome, latency_ms, constraint_len, model_version, reason)`. Metrics do not store prompt or completion text.

## 7. Enums & Constants

### Recommendation stance

| Value | Meaning |
| --- | --- |
| intensity | Harder training is acceptable; may surface higher-intensity cardio. |
| moderate | Default training stance. |
| recovery | Lower-stress session; used for poor readiness, excessive fatigue/load, or deload modifiers. |

### Source priority and influence

The engine uses a conservative source hierarchy rather than a single winner:

| Source | Role | Can modify recommendation? | Conditions |
| --- | --- | --- | --- |
| Open Wearables | Local hub wrapper and public wearable facts | Yes, conservative downgrade only | Fresh/aging facts for profile; sleep duration < 360 min or active minutes >= 90. |
| WHOOP | Recovery/strain/sleep modifier | Yes, bounded modifier | Fresh/aging fact, `score_state=SCORED`, and at least one relevant metric. |
| Oura | Readiness, sleep, HRV, conflict comparison | Yes through base deterministic readiness rules; conflict proof with WHOOP | Oura daily cache present. |
| Apple Health | Workload/cardio/strength load and fallback load source | Yes through deterministic load/readiness calculations | Sync log/workouts available; HR load rules from Apple Health bridge. |
| Food logs | Fueling and recovery context | Yes via adaptation event engine | Accepted entries only, after 180-second coalescing window. |
| Weather | Conservative environmental context | Yes, downgrade only | Cached wttr.in available. |

Recent hide-stale behavior: when the plan's load source is Apple Health, Open Wearables is actively used for recommendation, and Apple Health is missing/stale/error/blocked, the payload sets `load_source_summary_hidden=true` so legacy stale-source copy is not emphasized.

### Freshness

| Value | Rule |
| --- | --- |
| missing | No usable data point. |
| fresh | Data point age < 24 hours. |
| aging | Data point age >= 24 and < 48 hours. |
| stale | Data point age >= 48 hours. |

### WHOOP thresholds

| Signal | Threshold | Modifier |
| --- | --- | --- |
| Recovery score < 45 | Low recovery band | `deload`; recommendation becomes recovery, volume scale 0.8, RPE -1. |
| Recovery score < 60 | Conservative recovery | `caution`; downgrade one level, volume scale 0.9, RPE -1. |
| Strain >= 18 | High strain | `deload` if not already deload. |
| Strain >= 15 | Elevated strain | `caution` if not already caution. |
| Sleep performance < 70% | Meaningfully low sleep | `deload` if not already deload. |
| Sleep performance < 85% | Below target sleep | `caution` if not already caution. |
| Sleep need gap >= 60 min | Sleep gap | Adds `sleep_priority` and `fuel_up` explanations. |
| WHOOP recovery band | <45 low, <67 medium, otherwise high | Used for Oura conflict comparison. |

### Open Wearables thresholds

| Fact | Threshold | Modifier |
| --- | --- | --- |
| `sleep_duration` | < 360 minutes | `sleep_caution`; recommendation held conservative. |
| `active_minutes` | >= 90 minutes | `activity_caution`; recommendation held conservative. |

### Food adaptation constants

| Constant | Value | Meaning |
| --- | --- | --- |
| `COALESCING_WINDOW_SECONDS` | 180 | Wait after food acceptance before evaluating. |
| `MIN_WORKOUT_CONFIDENCE` | 0.65 | Minimum food confidence for any workout change. |
| `UNDER_FUELED_CALORIES_PCT` | 60 | Calories below this percent can trigger same-day volume reduction. |
| `UNDER_FUELED_PROTEIN_PCT` | 50 | Used by app-level under-fueled warning logic. |
| `LOW_PROTEIN_PCT` | 80 | Protein below this percent can trigger same-day volume reduction. |
| `SODIUM_RECOVERY_CONTEXT_MG` | 2300 | Sodium at/above this can trigger next-day recovery context. |
| `LATE_MEAL_HOUR` | 20 | Meal logged at/after 8pm can trigger next-day recovery context. |
| `HEAVY_MEAL_CALORIES` | 900 | Heavy meal signal; alone does not change workout. |
| Mostly-carbs rule | calories >= 250, carbs >= 45g, carbs >= 2x protein+fat grams | Mostly-carbs signal; alone does not change workout. |

### Food adaptation statuses and decisions

| Value | Meaning |
| --- | --- |
| `applied` | Plan was patched and event should be visible. |
| `no_change` | Decision produced no plan change. |
| `reduce_volume` | Same-day under-fueled/low-protein signal reduces remaining sets by one per exercise, preserving completed work. |
| `rest_recovery` | Next-day alcohol/late/high-sodium context shifts plan toward recovery. |
| `remove_strength_sets` | Supported by patcher but not currently selected by decision rules observed. |
| `none` | No operation. |
| `low_confidence` | Food confidence below 0.65. |
| `no_science_supported_change` | Conservative rules did not support changing the plan. |
| `stale_window` | Pending window no longer applies to the plan. |

### AI coach constants

| Constant/env | Default | Meaning |
| --- | --- | --- |
| `LM_STUDIO_PRIMARY_URL` / `LM_STUDIO_URL` | `http://127.0.0.1:1234` | Primary OpenAI-compatible LM Studio endpoint. |
| `LM_STUDIO_PRIMARY_MODEL` / `LM_STUDIO_MODEL` | `qwen/qwen3-30b-a3b-2507` | Primary adjust/analyze model. |
| `LM_STUDIO_FALLBACK_URL` | `http://127.0.0.1:1234` | Fallback endpoint. |
| `LM_STUDIO_FALLBACK_MODEL` | `qwen/qwen3.6-35b-a3b` | Fallback model. |
| `LM_STUDIO_TIMEOUT_SEC` | 8 | Adjust generation timeout. |
| `LM_STUDIO_ANALYZE_TIMEOUT_SEC` | 25 | Analyze generation timeout. |
| `LM_STUDIO_SWAP_RESOLVE_TIMEOUT_SEC` | 2.5 | Swap resolution timeout. |
| `LM_STUDIO_PREFLIGHT_TIMEOUT_SEC` | 1.5 | `/v1/models` preflight timeout. |
| `MEAL_TEXT_LOCK_ACQUIRE_SEC` | 2.0 | Shared text inference lock timeout for meal text flows. |
| Adjust cache version | `fit265-honest-adjust-v2` | Cache invalidation version. |
| Analyze prompt version | `notes-v2` | Analyze cache/prompt version. |

## 8. Integration Points

- Daily brief reads this engine's recommendation, freshness, source proof, nutrition context, and next workout.
- Workout execution supplies `active_workout_open` and `completed_sets` so adaptations can preserve completed work.
- Food logging enqueues adaptation windows only after accepted food; pending review entries are proof-only until accepted.
- Data layer persists food logs, adaptation windows/events, wearable facts, WHOOP facts, and AI coach cache/metrics.
- Open Wearables sync writes public facts consumed by conservative modifiers and source proof.
- WHOOP sync/import writes daily facts consumed by `whoop_recommendations.py`.
- Apple Health recommendation bridge contributes workouts/load to training history, ACWR/cardio load, and AI history context.
- Oura cache contributes readiness, sleep, HRV, freshness, and source-conflict comparison.
- Auth/CSRF controls protect session routes and mutating AI/adaptation endpoints.

## 9. Permissions & Security

All documented routes are session-authenticated through the global auth guard except public/static paths outside this feature. Mutating browser routes rely on same-origin browser metadata or `X-Requested-With: XMLHttpRequest`; Apple Health token sync is the explicit CSRF-exempt external path, not these coach routes.

The AI fact context is deliberately sanitized. It includes source metadata, public facts, and normalized history; it does not include raw Open Wearables/WHOOP/Oura/Apple payloads, access tokens, refresh tokens, passwords, or secrets. `wearable_fact_store.py` rejects forbidden field names such as `authorization`, `access_token`, `refresh_token`, `token`, `password`, `secret`, `raw`, `payload`, `samples`, `records`, and `user_id`.

`/api/wearable-sources` and the AI fact context merge stored `wearable_sources` rows from `wearable_facts.sqlite3` with live computed source payloads, deduplicated by source key.

LM Studio calls are local by default. The adapter sends compact plan/context JSON to configured primary/fallback endpoints. The LLM returns intent only; Python validates and applies. Safety rails prevent direct weight prescription, broad plan replacement, deload override, large RPE/set jumps, and unavailable exercise substitutions.

Sensitive risk: `AI_PENDING_SUGGESTIONS` is in-memory and not profile- or worker-durable. It should not be treated as an audit-grade approval ledger.

## 10. Business Rules

The deterministic recommendation starts from Oura readiness, HRV trend, soreness, recent training, workload, sleep debt, recovery bonus, weather, and history. Effective readiness below 70 drives recovery; above 85 can support intensity; otherwise moderate is the base. HRV decline, sleep debt over 300 minutes, ACWR above 1.5, recent high-fatigue training, high heat, and cold weather can downgrade. ACWR above 1.5 forces recovery; ACWR 1.3-1.5 downgrades one level.

Soreness of 6 or higher in the last 24 hours adds that muscle to the avoid list. A completed workout within 18 hours adds muscles with at least two sets to the avoid list as recently trained. Recent trained muscles affect copy differently from soreness, but both protect the next session.

WHOOP can only modify when data is fresh or aging and scored. If WHOOP is stale, missing, unscored, calibrating, or has no relevant metrics, it is display-only. WHOOP and Oura conflicts are detected by band: Oura readiness <65 low, <80 medium, otherwise high; WHOOP recovery <45 low, <67 medium, otherwise high. A two-band disagreement creates a warning and keeps the more conservative source.

Open Wearables facts are profile-scoped and only facts with freshness `fresh` or `aging` participate. Open Wearables never increases training load; it only downgrades once when sleep or activity facts are concerning.

Accepted food is the only nutrition input allowed to alter workouts. Same-day calories below 60% or protein below 80% can reduce volume when confidence is at least 0.65. Alcohol, late meal, or high sodium apply only to next-day recovery context. Heavy meals or mostly-carbohydrate meals alone do not add punitive cardio or burn-off work.

Plan patches preserve completed work. Set reductions floor at the greater of one set or completed sets for each exercise. Time clamps first reduce strength work where allowed, then reduce cardio duration in 5-minute chunks; if the plan still exceeds available time, a cap-exceeded marker is emitted.

The LLM adjust path cannot override deterministic guardrails. RPE delta is constrained to -1 through +1, sets delta to -20% through +20%, target weight increases are capped at 10% over recent e1RM, and blacklisted sore/readiness muscles cannot be reintroduced.

## 11. Config & Environment

| Config | Default | Behavior when unset |
| --- | --- | --- |
| `DATA_DIR` | Repo root | Runtime SQLite/JSON/cache files live in the app directory. |
| `APPLE_HEALTH_SYNC_DB` | `DATA_DIR/apple_health_sync.db` | Apple Health freshness/load reads default local DB. |
| `FITNESS_DASHBOARD_PUBLIC_BASE_URL` | Derived from request | Used by adjacent integration URLs, not core recommendation logic. |
| LM Studio env vars | See section 7 | Local `127.0.0.1:1234` primary/fallback model defaults. |
| Open Wearables env/config | [TBC: detailed in Open Wearables PRD] | Missing config yields missing/stale source proof and no Open Wearables modifier. |
| WHOOP OAuth/config env | [TBC: detailed in WHOOP PRD] | Missing/disconnected WHOOP is display-only/missing. |

## 12. Test Coverage

Existing focused tests cover:

- `tests/test_ai_fact_context.py`: sanitized strength-history answers, route context excluding raw payload words, approval-required suggestion policy, and no suggestion without evidence.
- `tests/test_ai_health_metrics.py`: adapter-missing health response, adapter payload, app-shell reload gate, no model trace leakage, empty metrics, fallback percent, warning threshold above 20%, recent metrics without prompt/completion, hours clamp, and DB failure.
- `tests/test_fit136_workout_adaptation.py`: coalescing, low-confidence no-change, under-fueled volume reduction and time clamp, no punitive heavy-meal cardio, next-day alcohol/sodium recovery, pending-day sodium behavior, next-day deferral, alcohol word-boundary matching, active-workout completed-set preservation, citations and neutral language, duplicate-window prevention, no stacked reductions, no-change preserving prior patch, missing triggers, stale windows, and previous-day meal isolation.
- `tests/test_dynamic_cardio_recommendations.py`: cardio rotation, recovery/intensity/moderate overrides, hidden context not consuming rotation, machine-safe cardio choices, recent cardio avoidance, follow-up fatigue, dashboard readiness/cardio behavior, cached weather, and plan recomputation when readiness changes.
- `tests/test_apple_health_recommendation_bridge.py`: local start-date preference, Apple Health load and HR intensity effects, duration unit handling, duplicate workout/cardio dedupe, strength workout contribution to volume/readiness, ignored activity types, and smart recommendation Apple Health factors.
- `tests/test_recommendation_sources.py`: Open Wearables source-proof role/influence, profile scoping, and `load_source_summary_hidden=true` for stale Apple Health while Open Wearables is used across smart recommendation, dashboard, and next-workout route boundaries.

Coverage gaps:

- No observed persistence test for `AI_PENDING_SUGGESTIONS` because it is intentionally in-memory.
- No single integration test observed for simultaneous Open Wearables + WHOOP + food adaptation ordering.
- No observed test that `/api/recommendation/smart` is side-effect-free; it currently may evaluate adaptation windows.

## 13. Gaps & Issue Candidates

### IC-1: Wire live adaptation active-workout params from the client
- **Type:** Data-contract
- **Priority:** high
- **Where:** app.py:8371; app.py:14215
- **Problem:** The event feed and smart recommendation routes only treat an active workout as safely updatable when `completed_sets` are supplied. If the client sets `active_workout_open=true` without completed sets, evaluation is skipped; if the client never sends completed-set state, the live adaptation contract is effectively incomplete.
- **Why it matters:** The owner can have a workout open while the server cannot safely apply or explain nutrition-driven changes to remaining work.
- **Acceptance criteria:**
  - Client polling sends active workout id/state and completed sets in the documented format.
  - Server preserves completed sets and records `active_workout.was_open=true` for live-safe evaluations.
  - Focused test covers active workout open with completed sets from the route boundary.
- **Duplicate-of:** FIT-257

### IC-2: Make recommendation and event polling read-only
- **Type:** Bug
- **Priority:** high
- **Where:** app.py:4821; app.py:5105; app.py:8371; app.py:14215; workout_adaptation.py:170
- **Problem:** `GET /api/workout-adaptation-events` and `GET /api/recommendation/smart` can evaluate due pending food windows and persist adaptation events before returning a response. That makes ordinary polling and recommendation reads mutate state.
- **Why it matters:** Repeated page loads or background polls can create visible coaching state changes at surprising times and complicate retries.
- **Acceptance criteria:**
  - Side-effecting adaptation evaluation moves behind an explicit mutation or idempotent job boundary.
  - Polling/list routes only read persisted events.
  - Duplicate polls cannot create, consume, or acknowledge events.
  - Existing FIT-136 event tests are updated to cover the new boundary.
- **Duplicate-of:** FIT-233

### IC-3: Prorate partial-day fueling before reducing workout volume
- **Type:** Improvement
- **Priority:** high
- **Where:** workout_adaptation.py:418; app.py nutrition context around under-fueled warnings
- **Problem:** Same-day under-fueled and low-protein decisions compare current accepted food totals against full-day targets. Early-day accepted food can therefore look under-fueled even when the owner has not had a full day to eat.
- **Why it matters:** The coach may reduce workout volume too aggressively based on timing rather than true under-fueling.
- **Acceptance criteria:**
  - Fueling thresholds account for time of day or intended workout time.
  - Tests cover morning, midday, and evening accepted-food scenarios.
  - Event reason metadata states whether a prorated or full-day target was used.
- **Duplicate-of:** FIT-227

### IC-4: Persist AI suggestions instead of using process memory
- **Type:** Data-contract
- **Priority:** medium
- **Where:** app.py:215; app.py:12339; ai_fact_query.py:99
- **Problem:** AI fact suggestions are stored only in `AI_PENDING_SUGGESTIONS`, an in-memory dictionary. Suggestions disappear after restart and can be inconsistent across multiple workers.
- **Why it matters:** Approval/rejection is not durable, so the owner cannot rely on suggestions as an auditable approval workflow.
- **Acceptance criteria:**
  - Pending suggestions are stored in SQLite with owner/profile scope.
  - Approve/reject is idempotent and survives restart.
  - Tests cover restart-like reloading and missing/approved/rejected states.
- **Duplicate-of:** FIT-256

### IC-6: Expose complete source-proof for all recommendation inputs
- **Type:** Improvement
- **Priority:** medium
- **Where:** app.py:12935; app.py:14215
- **Problem:** Source proof is strong for Open Wearables and WHOOP, but Oura and Apple Health influence can be spread across readiness, freshness, reasoning strings, and wearable source rows. There is no single per-source proof object listing exact fields used, whether each source modified the recommendation, and why stale sources were ignored.
- **Why it matters:** The owner needs to know not just what the coach said, but which data actually changed the plan.
- **Acceptance criteria:**
  - Recommendation response includes a per-source proof object for Open Wearables, WHOOP, Oura, Apple Health, food, and weather.
  - Each source lists `used_for_recommendation`, `fields_used`, `modifier_applied`, and `ignored_reason`.
  - Tests cover stale, missing, display-only, and modifying states.
- **Duplicate-of:** none

### IC-7: Add cross-source ordering tests for food, WHOOP, and Open Wearables
- **Type:** Test
- **Priority:** medium
- **Where:** app.py:14215; whoop_recommendations.py:181; workout_adaptation.py:170
- **Problem:** The smart route applies base recommendation logic, WHOOP, Open Wearables, food adaptation, then WHOOP workout patching again. Existing focused tests cover each subsystem, but no observed test locks the final ordering when multiple conservative sources fire together.
- **Why it matters:** Ordering bugs can double-apply volume reductions or hide the true reason a plan became conservative.
- **Acceptance criteria:**
  - Integration test seeds Oura, WHOOP, Open Wearables, and accepted food signals together.
  - Final response proves each modifier is applied at most once.
  - Reasoning and source proof preserve all contributing sources in deterministic order.
- **Duplicate-of:** none
