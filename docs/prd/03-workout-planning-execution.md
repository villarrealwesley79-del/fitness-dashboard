# Workout Planning & Execution — PRD

> **Sources:** `README.md`; `docs/VISION.md`; `docs/PRD.md`; `docs/CURRENT_STATE.md`; `app.py`; `workout_adaptation.py`; `data_store.py`; `templates/index.html`; `static/js/app.js`; `static/js/sw.js`; `tests/test_fit136_workout_adaptation.py`; `tests/test_fit136_workout_adaptation_api.py`; `tests/test_fit137_adaptation_ui.py`; `tests/test_fit187_active_workout_start_guard.py`; `tests/test_exercise_library_preferences.py`; `tests/test_exercise_disambiguation_taxonomy.py`; `tests/test_dynamic_cardio_recommendations.py`; `tests/test_offline_workout_sync.py`; `tests/test_workout_sync_queue_js.py`; `tests/test_history_detail_and_analyze.py`; route inventory scratchpad; existing open issue scratchpad.
> **Routes:** `/api/next-workout`, `/gym-now`, `/api/add-workout`, `/api/add-soreness`, `/api/add-cardio`, `/api/add-recovery`, `/api/exercises`, `/api/exercises/alternatives/<muscle_group>`, `/api/settings`, `/api/settings/equipment`, `/api/workout/swap`, `/api/workout/adjust`, `/api/workout/analyze`, `/api/workout-adaptation-events`, `/api/workout-adaptation-events/<event_id>/ack`, `/api/progressive-overload`, `/api/history`, `/api/history-all`, `/api/delete-history`, `/api/restore-history`, `/api/complete-workout`, `/api/auth/scope`.
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

## 1. Overview

Workout Planning & Execution turns recovery, soreness, training history, settings, equipment preferences, and nutrition context into a usable workout plan, then supports starting, modifying, logging, completing, and reviewing that workout. The product goal is not just to prescribe a plan; it is to preserve workout progress on mobile while the owner swaps exercises, adjusts constraints, loses connection, or receives a same-day adaptation.

The deterministic Python workout engine owns the plan. The AI layer can translate a natural-language constraint into an intent patch, but Python validates and applies the actual plan changes. Exercise swaps are deterministic first, with an optional LM Studio resolver only when free-text matching is inconclusive.

This PRD covers the user-visible planning and execution surface. Adaptation signal computation internals belong to PRD 11, but the visible adaptation event feed, live active-workout merge, and acknowledgement behavior are documented here because they affect workout execution.

## 2. User-Facing Surfaces

| Surface | Location | Purpose | Data source | States |
| --- | --- | --- | --- | --- |
| Dashboard start action | Dash tab | Primary entry into today's recommended workout. | `/api/next-workout`, cached dashboard fallback. | Start, no plan, fetch failure fallback, in-progress discard confirmation. |
| Next Workout tab | `tab-workout` | Full plan preview before execution. | `/api/next-workout`, async `/api/recommendation/smart`, async `/api/settings`. | Loading, loaded exercises, rest-day empty, cardio card, optional helper failure. |
| Active workout modal | `modal-active` | Mobile coach clipboard for set logging. | Client draft created from recommendation. | Open, dirty, recovered draft, saving, error, queued, complete. |
| Active workout set rows | Active modal | Log weight, reps, done checkbox, set notes. | Recommendation targets plus local edits. | Prefilled, edited, done, next incomplete highlighted. |
| Active cardio block | Active modal | Log recommended cardio follow-up. | Recommendation `cardio`. | Hidden, recommended, completed, edited, preserved across adjustment. |
| Swap exercise modal | `modal-swap` | Replace one exercise with same-muscle allowed alternative or typed custom name. | `/api/exercises/alternatives/<muscle>`, `/api/workout/swap`. | Loading alternatives, selected, custom typed, inline error. |
| Adjust Plan modal | `modal-adjust` | Natural-language plan adjustment and preview. | `/api/workout/adjust`. | Submitted, changed, unchanged/refused, fallback, cached, start adjusted plan. |
| Log tab | `tab-log` | Manual strength/cardio/recovery logging outside active workout. | `/api/exercises`, `/api/add-workout`, `/api/add-cardio`, `/api/add-recovery`. | Strength, cardio, recovery segmented panels. |
| History tab | `tab-history` | Review workouts, frequency, volume, top exercises, recent workouts. | `/api/history-all`, `/api/history`, `/api/delete-history`, `/api/restore-history`. | Range filters, type filter, empty states, delete/undo. |
| Saved workout confirmation | `modal-workout-saved` | Post-save summary and optional analysis. | `/api/complete-workout`, `/api/workout/analyze`. | Logged, already logged, queued for sync. |
| Sync queue | client localStorage | Retry failed/offline workout saves. | Local queue + `/api/complete-workout`. | Pending, auth_required, rejected, conflicted, synced. |
| Adaptation notice | Dashboard/passive surfaces | Confirms server-applied same-day nutrition adjustment. | `/api/workout-adaptation-events`. | Hidden, applied update, ack retry, live active-workout merge. |
| Emergency gym view | `/gym-now` | Minimal no-app-shell plan surface for stale mobile/PWA caches. | Server-generated plan. | No-store HTML. [TBC: full rendered fields not inspected beyond route existence.] |

## 3. Field Inventory

### Next Workout Plan

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | string timestamp | yes | current `%Y%m%d%H%M%S` | Generated server-side. | Recommendation id for adherence tracking. |
| `created_at` | ISO datetime | yes | now | Server local time. | Plan creation timestamp. |
| `auth_scope` | string | client-required for drafts | `user:<current_data_user_id>` | Added by API wrapper. | Prevents one signed-in scope from restoring another scope's workout draft. |
| `focus` | string | yes | derived | Full Body, Upper Body, Lower Body, General. | Workout type. |
| `goal` | enum string | yes | settings training goal | Must be in `GOAL_PARAMETERS`. | Training objective. |
| `goal_name` | string | yes | goal display name | From `GOAL_PARAMETERS`. | User-facing goal label. |
| `estimated_duration` | string | yes | `{estimated_minutes} min` | Derived. | Display duration. |
| `estimated_minutes` | integer | yes | computed | Warmup/cooldown + exercise/cardio time. | Time budget fit. |
| `available_time` | integer minutes | yes | settings value | Settings validation 10-240; time options 20-90. | User's available session time. |
| `mesocycle.week` | integer enum | yes | computed | 1-4 based on completed workout count and weekly target. | Current training phase. |
| `mesocycle.phase` | string | yes | from plan | Accumulation, Overreach, Intensification, Deload. | Periodization context. |
| `mesocycle.volume_multiplier` | number | yes | phase-specific, may be reduced by readiness | Rounded to 2 decimals. | Set-volume scaling. |
| `mesocycle.rpe_base` | number | yes | phase-specific | From `MESOCYCLE_PLAN`. | Baseline effort. |
| `exercises` | array | yes | computed | Filtered by muscle readiness/equipment/user exclusions. | Strength prescription. |
| `cardio` | object/null | no | goal-specific | Included only if time remains and goal/cardio logic supports it. | Optional finisher/follow-up. |
| `muscles_to_avoid` | array | yes | empty | Muscles with readiness score < 5. | Avoid list. |
| `time_adjusted` | boolean | yes | `available_time < 60` | Derived. | Indicates a constrained plan. |

### Exercise Prescription

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `exercise` | string | yes | selected from library | Must resolve to an allowed exercise for generated/swap paths. | Exercise name. |
| `muscle` | string | yes | library muscle | Same-muscle rule enforced for swap. | Primary muscle group. |
| `is_compound` | boolean | yes | library value | From exercise definition. | Affects sorting and RPE range. |
| `target_weight` | number lb | yes | `max(5, derived target)`; Plank is special-cased to 0 but the final `max()` converts it back to 5 (see IC-8). | Derived from e1RM, baseline, similar history, and progression. | Suggested load. |
| `target_reps` | integer | yes | midpoint of goal rep range | Progressive overload can add 1 rep. | Suggested reps. |
| `target_sets` | integer | yes | at least 2 | Goal sets times volume multiplier; adaptation may reduce while preserving completed sets. | Suggested sets. |
| `rationale` | string | yes | generated | Includes goal and progression/load source note. | Why this prescription. |
| `rest_minutes` | string/number | yes | goal-specific | From goal parameters. | Rest guidance. |
| `rest_label` | string | yes | `{rest_minutes} min` | Display-only. | UI rest chip. |
| `rpe_target` | number | yes | computed 0.5 increments | Clamped by mesocycle, soreness, Oura readiness, exercise type. | Effort target. |
| `estimated_time` | integer minutes | yes | sets * time_per_set | Recomputed after adaptation. | Time budget. |
| `days_since_trained` | integer | yes | 7 if unknown | Derived from volume history. | Recovery spacing. |
| `soreness` | integer 0-10 | yes | 0 | Latest 72-hour soreness. | Local soreness signal. |
| `oura_readiness` | integer/null | no | null | 0-100. | Wearable readiness used in plan. |
| `load_source` | enum | yes | source-dependent | See enums. | Why target load exists. |
| `load_e1rm` | number | yes | rounded | Derived. | Estimated strength. |
| `load_source_detail` | string | no | source-dependent | Displayed in load hint. | Load provenance. |
| `load_inference` | object | no | absent | Present for similar-history estimates. | Shows low/medium/high estimate confidence to user. |

### Active Workout Draft

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `ACTIVE_WORKOUT_DRAFT_KEY` | string | yes | `fit168:active-workout-draft:v1` | localStorage key. | Browser draft storage. |
| `ACTIVE_WORKOUT_DRAFT_VERSION` | integer | yes | `1` | Restore only matching version. | Draft schema version. |
| `activeWorkout.id` | string | yes | recommendation workout id or generated `w-...` | Reused as client workout id. | Idempotent sync identity. |
| `activeWorkout.recommendation_id` | string/null | no | recommendation id | Sent to completion endpoint. | Adherence reference. |
| `activeWorkout.focus` | string | yes | plan focus | Display/session type. | Workout label. |
| `activeWorkout.auth_scope` | string | yes for persisted draft | from recommendation/current auth scope | Must match current scope on restore. | Privacy boundary. |
| `activeWorkout.exercises[].logged_sets` | array | yes | prefilled from targets | Saved on every mutation/pagehide. | User's live set entries. |
| `logged_sets[].weight` | string | no | target weight string | Input value, sent as number on completion. | Actual load. |
| `logged_sets[].reps` | string | no | target reps string | Input value, sent as number on completion. | Actual reps. |
| `logged_sets[].done` | boolean | yes | false | Done checkbox. | Completion filter: if any checked, only checked valid sets are saved. |
| `logged_sets[].notes` | string | no | empty | Max 500 server-side. | Set notes. |
| `activeWorkout.cardio` | object/null | no | from recommendation | Preserved if edited/completed. | Cardio follow-up. |
| `activeWorkout.dirty` | boolean | yes | false | True after edits/removals/cardio changes. | Used for discard/reload guards. |
| `activeWorkout.saveState` | object/null | no | null | `{message, variant}`. | Inline save/recovery/error status. |
| `queuedForSyncReview` | boolean | no | false | Suppresses draft saving after rejected/conflicted queue state. | Prevents overwriting review state. |

### Completion Payload

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `id` / `client_workout_id` | string | no but preferred | generated client id | Max 80; chars limited to letters, digits, `-`, `_`, `:`, `.`. | Idempotent offline/online workout id. |
| `date` | `YYYY-MM-DD` string | yes | client local today | Server accepts provided string. | Workout date bucket. |
| `recommendation_id` | string/null | no | active recommendation id | Used to find plan. | Adherence comparison. |
| `session_type` | string | yes | focus or `general` | Non-empty string else `general`. | Workout type. |
| `duration_minutes` | integer | no | 45 server default, client often 0 | Clamped 0-600; cardio duration can be added. | Duration. |
| `exercises` | array | yes | none | Must contain at least one exercise; each exercise must have machine and at least one set. | Actual performed work. |
| `exercises[].machine` | string | yes | exercise name | Required. | Exercise performed. |
| `exercises[].muscle_group` | string | no | inferred from library/map or `unknown` | Server fills if missing. | Muscle classification. |
| `sets[].reps` | number | yes for saved set | client filters >0 | Server tolerates/coerces. | Completed reps. |
| `sets[].weight_lbs` | number | yes | client filters >=0 | Used for volume/e1RM. | Completed load. |
| `sets[].rpe` | number/null | no | exercise RPE | Stored per set. | Effort. |
| `sets[].set_number` | integer | no | set index + 1 | Server fills if missing. | Set order. |
| `sets[].notes` | string | no | omitted if blank | Max 500. | Set-specific notes. |
| `fatigue` | integer | no | null if absent | If provided, clamped 1-10. | Overall fatigue. |
| `notes` | string | no | empty | Max 2000. | Workout notes. |
| `cardio` | object/null | no | null | See below. | Cardio follow-up actuals. |

### Cardio Completion

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `cardio.completed` | boolean | yes if cardio object | false | Boolean cast. | Whether recommended cardio was done. |
| `cardio.activity_type` | string | no | recommendation type/machine or `Cardio` | Max 64. | Actual cardio type. |
| `cardio.duration_minutes` | integer | no | recommendation duration or 0 | Clamped 0-600. | Actual cardio minutes. |
| `cardio.avg_heart_rate` | integer/null | no | null | Client not currently in active cardio UI [TBC]; server supports. | Cardio intensity evidence. |
| `cardio.intensity` | integer | no | 5 | Clamped 1-10. | Subjective cardio intensity. |
| `cardio.notes` | string | no | empty | Max 2000. | Cardio notes. |
| `cardio.recommendation` | object | no | copied from plan | Projected fields only. | What was recommended. |

## 4. Interactions & Flows

### Generate Next Workout

Trigger -> Dashboard, Workout tab, Start Workout, smart recommendation, or gym-now route needs a plan.  
Behavior -> Server fingerprints plan inputs and reuses `LAST_WORKOUT_RECOMMENDATION` only when fingerprint matches. It computes training recommendation, applies Open Wearables/WHOOP modifiers, generates exercises and cardio, and may replay due food adaptation windows.  
Validation -> Settings goal must exist; exercise library is filtered by equipment preference and exclusions; unavailable muscles/readiness < 5 are avoided.  
API -> `/api/next-workout`.  
Success -> Returns `next_workout` with auth scope and source attribution.  
Failure -> Client falls back to cached next workout or dashboard plan before showing "No workout planned."

### Start Workout

Trigger -> User taps Start Workout from dashboard or Workout tab.  
Behavior -> Client fetches `/api/next-workout` with force, falls back to cached plan, confirms before discarding an in-progress workout with logged progress, then creates `state.activeWorkout` and opens the active modal.  
Validation -> If active workout has progress, browser confirm text is "You have an in-progress workout. Discard logged sets and restart?"  
API -> `/api/next-workout`; optional `/api/dashboard` fallback.  
Success -> Active modal opens with prefilled set rows and optional cardio.  
Failure -> Toast "No workout planned."

### Log Active Sets

Trigger -> User edits weight/reps/notes or toggles set done.  
Behavior -> Client updates active workout state, marks it dirty, saves auth-scoped draft to localStorage, updates sticky progress header, highlights next incomplete set, and scrolls to next row when a set is completed.  
Validation -> Draft only persists when an auth scope is known; restore requires live matching scope.  
API -> none until completion.  
Success -> Set progress survives tab switches, pagehide, and auth-scope refresh.  
Failure -> localStorage unavailable keeps state in memory for current page only [TBC: no visible warning observed].

### Swap Exercise

Trigger -> User opens swap from plan preview or active workout.  
Behavior -> Client loads same-muscle alternatives and posts selected/custom name to `/api/workout/swap`. Backend validates plan, exercise index, same muscle, equipment preference, exclusion list, and no-op swaps. It rebuilds the exercise prescription and returns the updated recommendation.  
Validation -> `new_exercise_name` required max 128; `exercise_index` in range; typed custom names can resolve by alias/token, then optional LLM candidate resolver.  
API -> GET `/api/exercises/alternatives/<muscle_group>`; POST `/api/workout/swap`.  
Success -> Recommendation updates; active workout swap path preserves already logged work where applicable.  
Failure -> Structured errors: unknown exercise 404, muscle mismatch 400, current exercise no-op 400, excluded/equipment-disallowed 400.

### Adjust Plan

Trigger -> User enters a natural-language constraint.  
Behavior -> Backend gets/creates current recommendation, detects deterministic exercise requests, then either applies a deterministic fallback or asks LM Studio for intent. Python applies only validated changes and updates the canonical recommendation.  
Validation -> Constraint required max 280; LLM intent schema requires `avoid_muscles`, `avoid_joints`, `swap`, `rpe_delta`, `sets_delta_pct`, `duration_cap_min`, `drop_cardio`. RPE delta clamps to +/-1.0; sets delta clamps to +/-20%; poor readiness or deload blocks upward changes; duration cap trims; cardio drops only on explicit intent.  
API -> POST `/api/workout/adjust`.  
Success -> Returns `status:"ok"`, `result_kind` `changed|unchanged|refused`, patched recommendation, summary, applied notes, cache metadata.  
Failure -> Returns `status:"fallback"` with unchanged deterministic recommendation, or deterministic fallback for recognized swap when model unavailable.

### Live Nutrition Adaptation

Trigger -> Accepted food creates a pending adaptation window; workout/dashboard reads occur after the 180-second coalescing window.  
Behavior -> Server evaluates due windows and can apply `reduce_volume` for under-fueled/low-protein same-day context or `rest_recovery` for next-day alcohol/late/high-sodium context.  
Validation -> Minimum confidence is 0.65; below-threshold events are silent no-change. Same-night next-day-only signals stay pending for next day. Completed sets are preserved when active workout counts are supplied.  
API -> `/api/next-workout`, `/api/dashboard`, `/api/recommendation/smart`, `/api/workout-adaptation-events`.  
Success -> Same-day applied events can patch active workout and show passive notice.  
Failure -> No-change events are persisted/auditable but not shown on dashboard.

### Complete Workout

Trigger -> User taps Complete Workout.  
Behavior -> Client builds completion payload from completed/valid set rows, disables the button and shows saving status, then posts to `/api/complete-workout`. If offline or server unavailable, payload is queued locally.  
Validation -> Client requires at least one set; server requires non-empty exercises, machine per exercise, at least one set per exercise, notes limits, id character limits, duration/fatigue clamps.  
API -> POST `/api/complete-workout`.  
Success -> Server inserts workout, computes adherence, optional cardio log, clears cached recommendation, invalidates client caches, opens saved confirmation.  
Failure -> 409 conflict, 401/403 auth, 4xx rejected, and 5xx/pending states are queued or shown for review.

### Offline Workout Sync

Trigger -> Complete workout while offline or when server cannot accept the save.  
Behavior -> Client stores payload in localStorage queue `fit51:sync-queue:v1`, retries pending/auth-required entries when online, and displays recoverable failure reasons.  
Validation -> Server idempotency uses client workout id and fingerprint. Same id/same fingerprint returns `already_synced`; same id/different fingerprint returns 409 `sync_conflict`; a request with an existing client id and no `exercises` key returns `already_synced` without validation. Empty exercises, missing machine/sets, over-limit notes, or invalid id characters return `sync_status:"rejected"`; numeric duration/fatigue coercion failures silently fall back to defaults (45 / 5). Replays without fatigue can match an existing entry whose `overall_fatigue` is 5 through a legacy fingerprint tolerance.
API -> POST `/api/complete-workout`, GET `/api/auth/scope`.  
Success -> Queue entry removed on `inserted` or `already_synced`. Adherence can be tri-state: `followed` is `null` when a named `recommendation_id` cannot be resolved.
Failure -> Entry remains with `pending`, `auth_required`, `rejected`, or `conflicted` status.

### History Review and Delete/Restore

Trigger -> User opens History tab or deletes/restores an item.  
Behavior -> Client fetches combined history, renders frequency/volume charts, top exercises, filters, and recent workouts. Delete uses entry type plus sorted index; restore appends the returned deleted object.  
Validation -> Entry type must be `workout`, `cardio`, or `recovery`; index must exist; restore entry must be object with date.  
API -> `/api/history-all`, `/api/history`, `/api/delete-history`, `/api/restore-history`.  
Success -> History updates and undo can restore deleted object.  
Failure -> 400 invalid type/index payload; 404 index out of range; 500 save failures.

## 5. API Endpoints

| Method | Path | Auth | Trigger | Key params | Response shape | Real/Mock |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/next-workout` | Required | Plan preview/start | `active_workout_open`, `completed_sets` | `next_workout`, `workout_adaptation_events`, `recommendation_sources` | Real |
| GET | `/gym-now` | Required | Emergency gym view | none | no-store HTML workout view; regenerates a lightweight Open-Wearables-free cached plan marker that `/api/dashboard` refuses to reuse but `/api/next-workout` may reuse when the fingerprint matches | Real |
| POST | `/api/add-workout` | Required + CSRF/same-origin | Legacy manual strength log | JSON workout | status/workout | Real, legacy |
| POST | `/api/add-soreness` | Required + CSRF/same-origin | Soreness input | muscle, soreness_level, notes | status/soreness | Real |
| POST | `/api/add-cardio` | Required + CSRF/same-origin | Manual cardio log | activity, duration, HR, intensity, notes | status/cardio | Real |
| POST | `/api/add-recovery` | Required + CSRF/same-origin | Recovery log | modality, duration, temp, notes | status/recovery | Real |
| GET | `/api/exercises` | Required | Exercise dropdown/library | `include_excluded` | `exercises` array | Real |
| GET | `/api/exercises/alternatives/<muscle_group>` | Required | Swap modal | path muscle | alternatives with load hints | Real |
| GET/POST | `/api/settings` | Required; POST CSRF/same-origin | Goal/time/equipment/preferences | settings JSON | settings/options | Real |
| PUT | `/api/settings/equipment` | Required + CSRF/same-origin | Quick equipment change | equipment_preference | status/equipment | Real |
| POST | `/api/workout/swap` | Required + CSRF/same-origin | Swap exercise | workout_index, exercise_index, new_exercise_name | status/recommendation | Real |
| POST | `/api/workout/adjust` | Required + CSRF/same-origin | Adjust Plan | constraint | status, result_kind, recommendation, notes | Real with deterministic fallback |
| POST | `/api/workout/analyze` | Required + CSRF/same-origin | Saved workout analysis | workout ref/payload | analysis payload | Real AI/fallback; internals PRD 11 |
| GET | `/api/workout-adaptation-events` | Required | Poll adaptation notices | unacknowledged, since, limit, active params | events/count | Real |
| POST | `/api/workout-adaptation-events/<event_id>/ack` | Required + CSRF/same-origin | Dismiss adaptation | event id | status/id | Real |
| GET | `/api/progressive-overload` | Required | Progress review | none | exercise overload data | Real |
| GET | `/api/history` | Required | Workout history | none | workouts/count | Real |
| GET | `/api/history-all` | Required | Combined history tab | none | workouts/cardio/recovery/PRs | Real |
| POST | `/api/delete-history` | Required + CSRF/same-origin | Delete history row | type,index | status/deleted | Real |
| POST | `/api/restore-history` | Required + CSRF/same-origin | Undo delete | type,entry | status/restored | Real |
| POST | `/api/complete-workout` | Required + CSRF/same-origin | Complete active workout/offline retry | completion payload | status, sync_status, adherence, workout_id | Real |
| GET | `/api/auth/scope` | Required | Draft/queue ownership | none | `auth_scope` | Real |

## 6. Data Model & Persistence

| Store | File/table | Fields | Retention/behavior |
| --- | --- | --- | --- |
| Workouts | `DATA_DIR/data_workouts.json` | Completed workout entries, adherence, offline sync metadata. | Appends on complete/add; malformed JSON moved aside and default recreated. |
| Soreness | `DATA_DIR/data_soreness.json` | date, muscle, soreness_level, notes, created_at. | Recent entries influence readiness/avoid list. |
| Cardio | `DATA_DIR/data_cardio.json` | date, activity_type, duration, avg HR, intensity, notes, source. | Manual and completed-workout cardio. |
| Recovery | `DATA_DIR/data_recovery.json` | date, recovery_type, duration, temperature, notes. | Feeds recovery bonus. |
| Settings | `DATA_DIR/data_settings.json` | training goal, time, targets, equipment, exclusions, landmarks. | POST writes atomically; plan cache cleared. |
| Workout recommendations | in-memory `WORKOUT_RECOMMENDATIONS`, `LAST_WORKOUT_RECOMMENDATION` | Recommendation objects. | Not durable; fingerprint decides reuse. Multi-worker safety is an open risk. |
| Active workout draft | browser localStorage `fit168:active-workout-draft:v1` | version, saved_at, auth_scope, workout. | Restores only for matching live auth scope. |
| Workout sync queue | browser localStorage `fit51:sync-queue:v1` | client_workout_id, attempts, last_status, payload, reject_reason. | Retryable statuses: pending and auth_required. |
| Adaptation pending/events | SQLite `workout_adaptation_pending`, `workout_adaptation_events` in data store | windows, event payload JSON, acknowledged_at. | Pending windows coalesce for 180 seconds and are processed once by claim/update. |
| AI adjust cache | `DATA_DIR/ai_coach_cache.sqlite3` | recommendation/constraint/readiness/model/equipment keyed result. | Used for adjust response caching. |

## 7. Enums & Constants

### Training Goals

| Value | Name | Rep range | Sets/exercise | Rest | RPE | Intensity % | Volume multiplier | Time/set |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `strength` | Strength | 1-5 | 5 | 3-5 min | 8.5 | 85 | 0.8 | 5 |
| `hypertrophy` | Hypertrophy | 8-12 | 4 | 1.5-2 min | 7.5 | 70 | 1.2 | 3 |
| `endurance` | Muscular Endurance | 15-25 | 3 | 0.5-1 min | 6.5 | 50 | 1.0 | 2 |
| `weight_loss` | Weight Loss | 8-12 | 3 | 1.5-3 min | 7 | 70 | 1.3 | 3 |
| `toning` | Toning | 8-15 | 3 | 1-2 min | 6.5 | 65 | 1.0 | 2 |
| `strength_hypertrophy` | Strength + Hypertrophy | 5-8 | 4 | 2-3 min | 8 | 78 | 1.0 | 4 |
| `hypertrophy_endurance` | Hypertrophy + Endurance | 10-15 | 4 | 1-1.5 min | 7 | 65 | 1.1 | 2.5 |
| `weight_loss_toning` | Weight Loss + Toning | 8-15 | 3 | 1-2 min | 7 | 65 | 1.2 | 2 |

### Settings Defaults and Options

| Name | Values |
| --- | --- |
| Default training goal | `strength_hypertrophy` |
| Default sessions/week | `3` |
| Default available time | `75` minutes |
| Default target weight/body fat | `175 lb`, `18%` |
| Default nutrition targets | `2200` calories, `148 g` protein |
| Default fatigue threshold | `72` |
| Equipment preference | `machines_only`, `machines_and_cables`, `all` |
| Default preferred brands | `Hoist`, `Nautilus` |
| Default excluded exercises | `Preacher Curl` |
| Time options | 20, 30, 45, 60, 75, 90 minutes |
| Sex options | blank/not set, `female`, `male`, `nonbinary`, `prefer_not_to_say` |
| Volume landmarks default | mv 6, mev 9, mav_min 12, mav_max 18, mrv 22 |

### Mesocycle Plan

| Week | Phase | Volume multiplier | RPE base |
| --- | --- | --- | --- |
| 1 | Accumulation | 1.0 | 7.0 |
| 2 | Overreach | 1.2 | 7.5 |
| 3 | Intensification | 0.8 | 8.5 |
| 4 | Deload | 0.5 | 5.5 |

### Cardio Constants

| Name | Values/meaning |
| --- | --- |
| HR zones | Zone 1 95-114 BPM, Zone 2 114-133 BPM, Zone 3 133-152 BPM, Zone 4 152-171 BPM, Zone 5 171-190 BPM. Assumes max HR 190. |
| Cardio modality pools | Endurance: Outdoor run, Treadmill run, Bike, Rower, Stairmaster. Weight loss: Stairmaster, Treadmill incline walk, Bike, Elliptical, Rower. Toning: Treadmill incline walk, Elliptical, Bike, Stairmaster. Hybrid hypertrophy/endurance: Stairmaster, Bike, Rower, Treadmill incline walk, Elliptical. Hybrid weight loss/toning: Stairmaster, Treadmill incline walk, Bike, Elliptical, Rower. |
| Recovery modalities | Treadmill incline walk, Bike, Elliptical, Outdoor walk. |
| Outdoor modalities | outdoor run, outdoor walk; filtered out for machine-only/gym-safe preferences. |
| Dynamic signals | Recovery recommendation forces Zone 2 walk-class cardio; intensity forces Zone 4; moderate forces Zone 3; hidden context generation does not consume rotation cursor. |

### Workout Adaptation Constants

| Name | Value | Meaning |
| --- | --- | --- |
| `COALESCING_WINDOW_SECONDS` | 180 | Accepted food logs within 3 minutes merge into one adaptation window. |
| `MIN_WORKOUT_CONFIDENCE` | 0.65 | Minimum food confidence for workout changes. |
| `UNDER_FUELED_CALORIES_PCT` | 60 | Calories below this percentage can reduce same-day volume. |
| `UNDER_FUELED_PROTEIN_PCT` | 50 | Defined constant [TBC: not directly used in observed `_nutrition_signals`; `LOW_PROTEIN_PCT` is used]. |
| `LOW_PROTEIN_PCT` | 80 | Protein below this percentage creates low-protein signal. |
| `SODIUM_RECOVERY_CONTEXT_MG` | 2300 | High sodium next-day context threshold. |
| `LATE_MEAL_HOUR` | 20 | 8 PM or later is a late-meal signal. |
| `HEAVY_MEAL_CALORIES` | 900 | Heavy-meal signal threshold. |
| Adaptation statuses | `applied`, `no_change` | User-visible changes vs silent/no-change events. |
| Adaptation change types | `reduce_volume`, `rest_recovery`, `remove_strength_sets`, `none` | Conservative patch types. |
| Patch operations | `reduce_sets`, `clamp_reduce_sets`, `clamp_cardio_minutes`, `cap_exceeded`, `set_recovery_note` | How remaining plan was changed. |

### Sync Statuses

| Status | Meaning |
| --- | --- |
| `inserted` | Server saved new workout. |
| `already_synced` | Same client id/fingerprint already exists. |
| `pending` | Retry later; usually offline/network/server 5xx. |
| `auth_required` | Sign-in mismatch or auth failure; retryable after correct sign-in. |
| `rejected` | Server rejected payload; user should review queue. |
| `conflicted` | Same client id but different fingerprint exists. |

### AI Adjust Intent Contract

| Field | Type | Meaning |
| --- | --- | --- |
| `avoid_muscles` | list of strings | Remove or avoid exercises for these muscles. |
| `avoid_joints` | list of `{side,joint}` | Avoid exercises loading side-specific joints. Valid side/joint values are enforced by adapter [TBC: exact enum list lives in `lm_studio_adapter.py` validation]. |
| `swap` | list of objects | Replacement requests with replace exercise/target muscle/target exercise. |
| `rpe_delta` | number -1.0 to +1.0 | Effort adjustment. |
| `sets_delta_pct` | number -20 to +20 | Volume adjustment. |
| `duration_cap_min` | number | Maximum duration requested by user; `0` means no cap. |
| `drop_cardio` | boolean | True only when user explicitly requests no cardio or very short duration. |

## 8. Integration Points

| Feature | Coupling |
| --- | --- |
| Daily Brief Dashboard ([02-daily-brief-dashboard.md](02-daily-brief-dashboard.md)) | Shows plan headline/start action and passive same-day adaptation notice. |
| AI Coach PRD 11 | Provides adjust/analyze intent and narrative; deterministic engine applies/refuses changes. |
| Nutrition/Food PRDs | Accepted food schedules adaptation windows and can reduce volume or shift next day to recovery. |
| Wearables PRDs | Oura readiness, WHOOP modifiers, Apple Health HR intensity, and Open Wearables facts alter recommendation confidence/load. |
| Settings | Training goal, available time, sessions/week, equipment, brands, exclusions, profile fields, and targets change plan fingerprint. |
| History/Stats | Completed workouts feed progression, volume, recent completion, adherence, cardio fatigue, and future recommendations. |

## 9. Permissions & Security

Workout APIs are session-authenticated by default. Browser mutations use the `X-Requested-With: XMLHttpRequest` header or same-origin/form-CSRF path enforced in `auth.py`. Active workout drafts and sync queues remain on the device; active workout draft restore requires a matching live auth scope from `/api/auth/scope`. Offline sync failures keep payloads on the device until synced, retried, or discarded.

The app is local-first and single-owner by default. `LOGIN_DISABLED=True` appears only in tests/local proof patterns. State-changing endpoints should not be exposed cross-origin. The completion endpoint stores personal workout health data, so conflicts and auth-required states stay visible rather than silently discarding local data.

## 10. Business Rules

- Deterministic workout logic owns prescriptions; LLMs only return intent or narrative.
- Exercise selection prioritizes readiness score >= 5, compound movements under time pressure, higher readiness, least-recently-used exercise rotation, preferred equipment brands, and user exclusions.
- Oura readiness under 60 reduces volume multiplier by 20%.
- Effective workout time subtracts 10 minutes for warmup/cooldown.
- At least two resistance exercises are planned when possible.
- Cardio is included only when goal/time allow at least 10 minutes and enough resistance time remains.
- Same-muscle swap is mandatory; cross-muscle swaps are rejected.
- Excluded exercises are hidden by default and cannot be selected by swap.
- Active workout progress must be confirmed before starting a new workout.
- Adjusted plan previews restored from prior state must not mutate active workout unless the user starts/applies them.
- Adjustments merge into active workouts by exercise identity, not array index, to preserve completed sets.
- Completing a workout requires at least one valid completed set.
- If any set checkbox is checked for an exercise, only checked valid sets are saved for that exercise.
- Completing a workout clears the cached recommendation so the next plan reflects the completed session.
- `delete-history` uses sorted index; restore appends the original returned payload.

## 11. Config & Environment

| Config | Default | Behavior |
| --- | --- | --- |
| `DATA_DIR` | app-local path | Location for workout/settings/adaptation data. |
| `SECRET_KEY` | required for sessions in production | Auth/session security. |
| `FITNESS_DASHBOARD_SINGLE_USER` | `true` | Owner-only local app mode. |
| `FITNESS_DASHBOARD_OWNER_USER_ID` | min user id | Owner override. |
| `LM_STUDIO_*` | unset | Enables AI adjust/analyze; deterministic fallback otherwise. |
| `OW_*` | unset | Open Wearables facts/source attribution. |
| WHOOP config | unset | WHOOP modifiers and source conflicts. |
| Oura token/cache | optional | Readiness and sleep context. |

## 12. Test Coverage

| Test file | Coverage |
| --- | --- |
| `tests/test_fit136_workout_adaptation.py` | Adaptation windows, confidence gates, reduce-volume/rest-recovery rules, completed-set preservation, neutral language. |
| `tests/test_fit136_workout_adaptation_api.py` | Accepted food schedules windows; event endpoint contract; next-workout replay; active open without completed sets defers. |
| `tests/test_fit137_adaptation_ui.py` | Dashboard adaptation notice visibility, ack, passive rendering, no audit log, active workout merge hook. |
| `tests/test_fit187_active_workout_start_guard.py` | Start guard, adjusted plan start, active-workout identity merge, cardio preservation, set reduction behavior. |
| `tests/test_exercise_library_preferences.py` | Equipment preferences, exclusions, aliases, swap validation/fallback, brand ordering, joint metadata. |
| `tests/test_exercise_disambiguation_taxonomy.py` | Confusable exercise taxonomy schema and AI cache invalidation. |
| `tests/test_dynamic_cardio_recommendations.py` | Cardio rotation, readiness/intensity overrides, gym-safe cardio, dashboard high-readiness cardio. |
| `tests/test_offline_workout_sync.py` | Complete-workout idempotency, conflicts, rejected sync status. |
| `tests/test_workout_sync_queue_js.py` | Local draft persistence, auth-scope restore, queue retry statuses, background save. |
| `tests/test_history_detail_and_analyze.py` | History detail per-exercise sets and analyze route references. |

Coverage gaps: active-workout UI behavior is partially tested through Node/source fixtures rather than browser QA; `/gym-now` is lightly covered; delete/restore history is index-based and lacks stable-id coverage; multi-worker global state is a known risk.

## 13. Gaps & Issue Candidates

### IC-1: Wire active-workout adaptation polling with live set counts
- **Type:** Bug
- **Priority:** high
- **Where:** `static/js/app.js:510`, `app.py:8372`, `/api/workout-adaptation-events`
- **Problem:** The backend can preserve completed active-workout sets when `active_workout_open=true` and `completed_sets` are supplied, but the visible polling path uses `/api/workout-adaptation-events?unacknowledged=true&limit=10` without those parameters. That can defer or miss live-safe adaptation evaluation for an open workout.
- **Why it matters:** Same-day nutrition adjustments should not require a full next-workout reload or risk dropping completed work.
- **Acceptance criteria:**
  - Client sends active-workout-open state and completed set counts when polling.
  - Backend preserves completed sets and marks `active_workout.updated_live` accurately.
  - Regression test covers open workout with completed sets and applied same-day adaptation.
- **Duplicate-of:** FIT-257

### IC-2: Make adaptation event polling read-only unless explicitly evaluating
- **Type:** Data-contract
- **Priority:** medium
- **Where:** `app.py:8372`, `workout_adaptation.py:170`, `/api/workout-adaptation-events`
- **Problem:** The event-feed GET can evaluate due pending windows as part of polling. This makes a read-looking endpoint mutate pending windows and recommendation state.
- **Why it matters:** Polling should be predictable and idempotent, especially with retries, multiple tabs, and mobile reconnects.
- **Acceptance criteria:**
  - Separate "evaluate due adaptation windows" from "list events" or make mutation explicit.
  - Repeated GET polling cannot create duplicate events.
  - Existing processed-window claim safety remains intact.
- **Duplicate-of:** FIT-233

### IC-3: Keep applied workout adaptations visible until acknowledged
- **Type:** Improvement
- **Priority:** medium
- **Where:** `static/js/app.js:437`, `data_store.py:1065`, dashboard adaptation notice
- **Problem:** The client tracks seen event ids in memory, so a failed ack keeps the current card but a reload can re-fetch unacknowledged events. The product contract around visibility after reload, tab switch, or active-workout transition is not fully specified.
- **Why it matters:** The owner needs a stable record that the plan changed and why, without duplicate or disappearing cards.
- **Acceptance criteria:**
  - Applied events remain visible until successful ack across reloads.
  - Client avoids duplicate cards across polling/reload.
  - Stale source-meal contexts are invalidated or clearly labeled.
- **Duplicate-of:** FIT-230

### IC-4: Fix adjust/swap merge semantics and result kind honesty
- **Type:** Bug
- **Priority:** high
- **Where:** `app.py:8882`, `app.py:9399`, `static/js/app.js:7245`
- **Problem:** Swap and adjust flows update server/client recommendation state through several paths. Existing open work calls out merge-not-overwrite, honest `result_kind`, and versioned swaps, which overlap with observed complexity around cached plans, deterministic fallback, and active-workout identity merge.
- **Why it matters:** A workout edit must not silently overwrite logged work or claim a meaningful change when no user-visible change happened.
- **Acceptance criteria:**
  - Swap/adjust responses distinguish changed, unchanged, refused, fallback, and no-op accurately.
  - Active workout merges are versioned or otherwise protected from stale responses.
  - Tests cover active workout, next workout, and dashboard cached-plan update paths.
- **Duplicate-of:** FIT-265

### IC-5: Remove multi-worker global recommendation state corruption risk
- **Type:** Bug
- **Priority:** high
- **Where:** `app.py:4786`, `app.py:4924`, `app.py:8882`, `app.py:9399`, `app.py:14808`
- **Problem:** `LAST_WORKOUT_RECOMMENDATION` and fingerprint globals are server-process memory. In a multi-worker runtime, dashboard, swap, adjust, next-workout, and complete-workout can disagree about the canonical plan.
- **Why it matters:** The user could start one plan, swap/adjust another, then save adherence against the wrong recommendation.
- **Acceptance criteria:**
  - Canonical recommendation state is scoped and persisted or explicitly per-request.
  - Swap/adjust/complete endpoints reference stable recommendation ids.
  - Multi-worker test or documented single-worker guard proves behavior.
- **Duplicate-of:** FIT-256

### IC-6: Replace history delete-by-index with stable ids
- **Type:** Data-contract
- **Priority:** medium
- **Where:** `app.py:14637`, `app.py:14684`, `/api/delete-history`, `/api/restore-history`
- **Problem:** Delete-history accepts a type plus index in the current sorted list, then mutates the underlying original array. If the list changes between render and delete, the wrong row can be removed.
- **Why it matters:** Workout history is user-owned health data; accidental deletion breaks trust even with restore.
- **Acceptance criteria:**
  - History rows expose stable ids for workout/cardio/recovery entries.
  - Delete/restore operate by stable id and type, not sorted index.
  - UI still supports undo using the returned deleted payload.
- **Duplicate-of:** none

### IC-7: Add visible warning when active workout draft cannot persist
- **Type:** Improvement
- **Priority:** low
- **Where:** `static/js/app.js:7130-7150`, `saveActiveWorkoutDraft`
- **Problem:** Draft saving catches localStorage errors silently. If storage is unavailable, the user may believe the in-progress workout is recoverable when it is only in memory.
- **Why it matters:** A mobile browser storage failure during a workout can cause lost set notes and logged sets.
- **Acceptance criteria:**
  - Detect draft persistence failure and show a non-blocking warning.
  - Keep current in-memory behavior for the active page.
  - Add a JS test for localStorage failure path.
- **Duplicate-of:** none

### IC-8: Document or fix Plank target weight clamping
- **Type:** Bug
- **Priority:** low
- **Where:** `app.py:3384`, `_build_exercise_entry`
- **Problem:** The exercise builder special-cases Plank with `target_weight = 0`, but the returned field uses `max(5, target_weight)`, which appears to convert Plank back to 5 lb. It is unclear whether this is intentional.
- **Why it matters:** Timed bodyweight/core exercises should not show confusing load targets.
- **Acceptance criteria:**
  - Confirm intended Plank/bodyweight display contract.
  - If bodyweight timed work should show no load, return/display 0 or blank consistently.
  - Add a regression test for timed core prescriptions.
- **Duplicate-of:** none

### IC-9: Add browser QA for active workout modal states
- **Type:** Test
- **Priority:** medium
- **Where:** `templates/index.html:1614`, `static/js/app.js:7578`, active workout modal
- **Problem:** Active workout has many critical states: recovered draft, dirty close guard, swap, adjust, set completion, cardio completion, save error, queued, conflicted, and saved. Current coverage is strong but mostly Node/source-level rather than visual/mobile browser QA.
- **Why it matters:** Workout execution is the core mobile flow and layout/state regressions directly affect logging.
- **Acceptance criteria:**
  - Browser QA covers active workout modal on mobile viewport.
  - Include swap, adjust, delete/remove, queued, conflicted, empty, blocked, and warning states.
  - Verify bottom nav/modal controls do not overlap.
- **Duplicate-of:** none
