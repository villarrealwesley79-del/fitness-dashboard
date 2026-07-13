# Photo Food Logging / Vision Pipeline — PRD

> **Sources:** `README.md`, `docs/VISION.md`, `docs/PRD.md`, `docs/CURRENT_STATE.md`, `docs/FOOD_PHOTO_PRIVACY.md`, `docs/MEAL_MODEL_DECISION.md`, `docs/meal-model-benchmark-qwen3vl-macro-error-analysis-2026-05-24.md`, `vision_estimator.py`, `local_vision_adapter.py`, `claude_vision_adapter.py`, `lm_studio_adapter.py`, `meal_estimate_schema.py`, `meal_log_policy.py`, `app.py`, `data_store.py`, `templates/index.html`, `static/js/app.js`, `tests/test_fit221_vision_keep_warm.py`
> **Routes:** `POST /api/meal-intake`, `GET /api/meal-intake/pending`, `POST /api/meal-intake/<meal_id>/refresh`, `DELETE /api/meal-intake/<client_id>`, `POST /api/meal-intake/<client_id>/accept`, plus consuming nutrition routes `GET /api/nutrition-today`, `GET /api/nutrition-history`, `GET /api/food-logs/by-date/<date>`
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

## 1. Overview

Photo food logging lets the owner capture one or more food photos from the mobile PWA, optionally add a short text note, receive a structured nutrition estimate, and review the result before it counts toward the daily plan. The feature exists to reduce the friction of calorie/macronutrient logging while preserving trust: model output is always treated as an estimate, confidence is visible, ambiguous entries are blocked for review, and accepted values are editable.

The backend is real, not just a roadmap stub. `POST /api/meal-intake` accepts multipart text/photo input, calls the configured vision provider when photos are present, normalizes the result through the shared meal-estimate schema, persists a pending-review food log, saves a durable meal-review snapshot, and returns a review payload for the frontend. Accepting the review writes accepted/corrected `food_logs` rows and queues same-day workout-adaptation events.

The default provider is local LM Studio (`VISION_ESTIMATOR_PROVIDER=lm_studio`) using the served Qwen3-VL 30B-A3B route. The intended deployment language in the app UI names ASUS GX10 as primary and Mac Studio as fallback for the broader AI coach health card; the vision adapter itself only knows roles (`primary`, optional `low_memory`, optional `fallback`) and URLs/models from environment variables. Ollama and Claude adapters exist, but LM Studio is the promoted local route. Claude is a real external API adapter when `ANTHROPIC_API_KEY` is set, but it is not the default and is cloud/API fallback rather than local-first behavior.

Raw food photos are sensitive. The server contract discards raw image bytes after extraction and persists only final estimates, safe provenance, confidence/correction metadata, and review snapshots. The only current raw-photo retention exception is temporary browser-side offline queue storage in IndexedDB until a queued meal can be sent or discarded.

## 2. User-Facing Surfaces

### Log Tab Meal Composer

The main food capture surface is the "Log a meal" composer in `templates/index.html`. It contains:

| Region | Behavior |
|---|---|
| Camera/photo button | Opens a hidden file input with `accept="image/*"` and `multiple`, allowing mobile camera capture or library upload depending on browser behavior. |
| Barcode button | Opens the packaged-food barcode panel; covered only as a related intake path here. |
| Text input | Optional short context such as item name, serving clue, restaurant/order context, or correction hint. |
| Submit button | Disabled until there is text or at least one photo. Shows `Processing N photos...` online and `Save offline` when offline. |
| Thumbnail strip | Shows attached photo thumbnails with per-photo remove buttons and descriptive `aria-label`s. Removing a photo revokes its object URL and invalidates the draft client id. |
| Retention note | Displays "Photos are discarded after extraction" once photos are attached. |
| Offline banner | Explains that offline meals are saved on-device and sync when the browser reconnects. |
| Retry button | Appears after a transient network/server failure and reuses the same draft `client_id` to avoid duplicate pending rows. |
| Pending review list | Renders pending estimate cards returned from `/api/meal-intake` or hydrated from `/api/meal-intake/pending`. |

### Meal Review V2

When the backend returns a `meal_id` and `items[]`, the frontend uses the multi-item review surface. It shows meal totals, meal type selector, included/skipped/deleted item states, candidate chips, item expand/collapse, portion edit forms, follow-up questions, source chips, and save/delete controls. Blocked items show "Save blocked" until clarified, matched, edited, skipped, or deleted.

### Meal Detail Modal

Accepted food logs can be inspected from food-log surfaces. The modal shows item, portion, logged time, source, confidence, whether it came from a photo, calories, protein, carbs, fat, sodium, and a retention note for photo-originated rows. It also has a correction form for item, portion, calories, protein, carbs, fat, and sodium.

### Settings AI Coach Card

The Settings AI Coach card is not vision-specific, but it explains local AI routing: primary host label "ASUS GX10", fallback host label "Mac Studio", active state, model identity, and 24-hour metrics. The vision pipeline has its own environment variables and does not call `/api/ai/health` directly for food-photo estimation.

## 3. Field Inventory

### `POST /api/meal-intake` Multipart Request

| Field | Type | Required | Default | Validation | Business meaning |
|---|---|---:|---|---|---|
| `text` | string | No, unless no photo | `""` | Trimmed; max 500 chars | User context for the meal, portion, restaurant, label, or clarification. Sent to the vision prompt and/or text parser. |
| `images` | file list | No, unless no text | none | Up to 4 files; each must be image MIME; accepted MIME values: `image/jpeg`, `image/png`, `image/webp`, `image/gif`; each <= 6 MB; aggregate <= 18 MB | Canonical multi-photo input. All photos represent one meal and are sent in one combined vision call. |
| `image` | file | No | none | Same as `images`; accepted only when plural key is absent | Legacy single-photo key. |
| `client_id` | string | Yes | none | Non-empty; max 128 chars | Idempotency key for capture/retry and pending-review persistence. |
| `local_timestamp` | string | No | server time fallback | Max 64 chars | Legacy/browser timestamp. |
| `local_date` | string | No | derived from `local_iso`, `local_timestamp`, or server date | Max 10 chars | Browser-local meal date for day bucketing. |
| `local_iso` | string | No | derived fallback | Max 64 chars | Browser-local ISO timestamp with offset; preferred source for logged time/date. |

### Estimate Contract

| Field | Type | Required | Default | Validation | Business meaning |
|---|---|---:|---|---|---|
| `item_name` | string | Yes | none | Non-empty | User-readable food name. |
| `portion_description` | string/null | Yes in strict LM Studio schema; optional elsewhere | `null` | String or null | Portion/serving clue used in review and provenance. |
| `meal_type` | enum | Yes | `snack` in legacy/manual defaults | One of `breakfast`, `lunch`, `dinner`, `snack` | Meal bucket for logs and nutrition history. |
| `calories` | number | Yes | none, except manual fallback 0 | Numeric, >= 0, <= 5000 when plausible-range validation is active; rounded to integer | Calorie estimate or accepted value. |
| `protein_g` | number | Yes | none, except manual fallback 0.0 | Numeric, >= 0, <= 500 when plausible-range validation active; rounded to 0.1 g | Protein grams. |
| `carbs_g` | number | Yes | none, except manual fallback 0.0 | Same as protein | Carbohydrate grams. |
| `fat_g` | number | Yes | none, except manual fallback 0.0 | Same as protein | Fat grams. |
| `sodium_mg` | number | Yes | `0` in legacy/manual defaults | Numeric, >= 0, <= 12000 when plausible-range validation active; rounded to integer | Sodium estimate. |
| `fiber_g` | number | Yes | `0.0` in legacy/manual defaults | Numeric, >= 0, <= 500 when plausible-range validation active | Fiber grams. |
| `confidence` | number | Yes | `0.0` in manual fallback | 0.0 to 1.0, rounded to 2 decimals | Estimate trust score. |
| `ambiguous` | boolean | Yes | `true` in manual fallback | Must be boolean | Whether item identity, portion, ingredients, or serving is unclear. |
| `uncertainty_notes` | string[] | Yes | `[]` or fallback notes | List of strings | User-facing reasons to review or correct. |
| `source` | string | Yes | provider/fallback-dependent | Non-empty | Provenance class, such as `vision_lm_studio_estimate`, `vision_lm_studio+<lookup_source>`, `vision_lm_studio_label_ocr`, `manual_text_review`. |
| `items` | object[] | Required in strict LM Studio schema; optional public field | `[]` | LM Studio requires each item to include `item_name`, `quantity`, `brand`, `modifiers`, `portion_hint` | Structured components from carts, receipts, orders, or multi-item meals. |
| `label_ocr` | boolean | No | absent/false | Preserved only when true | Signals that the model read a nutrition/supplement facts label and macro values should follow printed serving data. |
| `from_image` | boolean | No | set server-side for photo-originated estimates | Boolean | Marks photo provenance without storing the raw image. |
| `vision_description` | string | No | none | Safe metadata, max 500 chars when preserved | Food description returned by the vision layer. |
| `vision_provider` | string | No | configured provider | Safe metadata | Provider used for the vision description. |
| `vision_confidence` | number | No | none | 0.0 to 1.0 | Original provider confidence before lookup confidence is combined. |
| `external_food_id`, `verified_source_url`, `data_fetched_at`, `portion_basis`, `brand_id`, `underlying_source`, `off_attribution`, `personal_vocab_phrase` | strings/object | No | none | Sanitized safe provenance only | Verified lookup attribution and vocabulary metadata. |

### Review Payload

| Field | Type | Required | Default | Validation | Business meaning |
|---|---|---:|---|---|---|
| `status` | enum | Yes | `pending_review` | `pending_review` for fresh capture responses | Capture always routes to review before counting. |
| `meal_id` | string | Yes | `client_id` | Max 128 from server route validation | Stable parent meal id. |
| `meal_type` | enum | Yes | first item meal type or `snack` | Same meal type enum | Top-level meal bucket. |
| `estimate` | object | Yes | sanitized estimate | Private fields stripped | Aggregate estimate used for legacy/single-item display. |
| `items` | object[] | Yes | one item if source estimate has no item array | Up to first 8 items from estimate | Reviewable meal components. |
| `food_log` | object/null | Yes | pending-review row | Server persisted | Pending food log row excluded from totals until accept. |
| `photo_retention` | object | Yes | policy payload | See privacy fields | User-visible retention guarantee. |
| `policy` | object | Yes | from `evaluate_meal_log` | Contains `confidence_band` and `reasons` | Explains why review is required. |
| `followup` | object | Yes | unavailable default | `available`, `question`, `used`, `target_item_id` | Optional clarification question for blocked/unclear items. |
| `local_timestamp`, `local_date`, `local_iso` | strings/null | No | request values | Copied from request | Preserve original meal day across retries and accepts. |
| `has_image` | boolean | Yes | false | Boolean | Indicates photo-originated review. |

### Photo Retention Payload

| Field | Type | Required | Default | Validation | Business meaning |
|---|---|---:|---|---|---|
| `policy` | string | Yes | `discard_after_extraction` | Constant | Raw photos are processing inputs, not durable records. |
| `raw_photo_retained` | boolean | Yes | `false` | Constant | Server does not keep raw image bytes. |
| `raw_model_trace_retained` | boolean | Yes | `false` | Constant | Prompts/raw completions/traces are not persisted. |
| `backup_includes_raw_photo` | boolean | Yes | `false` | Constant | Backups exclude raw photos. |
| `message` | string | Yes | Fixed copy | Constant | Human-readable privacy explanation. |
| `image_received` | boolean | Yes | false | Set per request | Confirms whether the request included a photo. |

### Offline Queue

| Field | Type | Required | Default | Validation | Business meaning |
|---|---|---:|---|---|---|
| `client_id` | string | Yes | generated UUID/random id | Key path in IndexedDB | Local idempotency key for queued meal. |
| `auth_scope` | string | Yes | cached from `/api/auth/scope` | Must match current scope before sync | Prevents syncing a meal under the wrong signed-in user. |
| `text` | string | No | `""` | Same composer text rules | Queued meal context. |
| `local_timestamp`, `local_date`, `local_iso` | strings | No | captured at queue time | Same request fields | Original meal date/time. |
| `image_count` | integer | Yes | 0 | Derived | Number of queued photo blobs. |
| `aggregate_bytes` | integer | Yes | 0 | Derived | Total queued photo bytes. |
| `image_metadata` | object[] | Yes | `[]` | No filenames | Safe per-photo metadata only. |
| `photo_ids` | string[] | Yes | `[]` | Key references | Links queued meal metadata to `meal_photos` blob rows. |
| `last_status` | enum | Yes | `pending` | `pending`, `auth_required`, `conflicted`, `rejected` | Queue sync status. |
| `attempts`, `last_attempt_at`, `server_response_summary`, `reject_reason` | mixed | No | null/0 | Updated on sync attempts | User-visible retry/debug state. |

## 4. Interactions & Flows

### Load / Hydrate Pending Reviews

Trigger -> The dashboard loads and meal composer initializes.
Behavior -> The frontend calls `GET /api/meal-intake/pending`, which removes pending rows older than 7 days, returns pending review payloads, and hydrates either V2 multi-item cards or legacy single-item cards.
Validation -> Server requires auth via global auth guard.
API -> `GET /api/meal-intake/pending`.
Success -> Pending estimates reappear after reload with review state intact.
Failure -> UI shows a toast that pending meals could not refresh.

### Add Photos

Trigger -> User taps the camera/photo control and chooses or captures files.
Behavior -> Browser validates count, MIME, per-file size, and aggregate size before adding accepted files to the composer. Thumbnails are shown with remove buttons. The draft id is reset because photo changes are material.
Validation -> Up to 4 photos; each `image/*` client-side, with backend restricted to JPEG/PNG/WebP/GIF; each <= 6 MB; aggregate <= 18 MB.
API -> None until submit.
Success -> Submit becomes enabled and retention copy appears.
Failure -> Already accepted photos stay attached; rejected files produce inline error copy.

### Submit Photo/Text Meal Online

Trigger -> User submits the composer while online.
Behavior -> Frontend builds `FormData`, appends all photos under `images`, sends browser-local timestamps, and posts to `/api/meal-intake`.
Validation -> Backend enforces content type, byte caps, text length, `client_id`, local timestamp lengths, MIME allowlist, and at least one of text/photo.
API -> `POST /api/meal-intake`.
Success -> Fresh capture always persists as `pending_review`, returns a review payload, clears the composer, renders a pending card, and refreshes the macro card without counting pending rows.
Failure -> 404/501 disables the meal backend UI; 5xx/network keeps draft and shows Retry; 4xx clears retry id because the request itself is invalid. Photo-only vision failure returns 503 with `reason=vision_estimator_failed` and the retention payload.

### Vision Estimate

Trigger -> `/api/meal-intake` receives at least one validated photo.
Behavior -> The backend calls `vision_estimator.describe(images=image_blobs, context_text=text_raw)`. The provider returns a cleaned description with confidence, ambiguity, uncertainty notes, optional macro estimate, optional label OCR, and optional structured item list.
Validation -> Provider output must include an item description. LM Studio uses strict JSON schema for full meal estimates and retries schema failures up to 2 additional attempts per candidate.
API -> Internal provider calls, not exposed as public routes.
Success -> The app tries structured item lookup or branded lookup unless label OCR is present. Verified lookup data is combined with vision metadata; otherwise macro estimates or manual low-confidence fallbacks are used.
Failure -> If text exists, backend falls back to text parsing/manual review and records `vision_error=vision_estimator_failed`; if no text exists, request fails with 503.

### Structured Item Lookup

Trigger -> Vision response includes `items[]`. Multi-item lookups are skipped when the user text contains a portion hint not reflected in the items; the top-level branded fallback is also text-gated via `_vision_lookup_allowed_for_text`.
Behavior -> Up to 8 item queries are built from quantity, brand, item name, modifiers, and portion hints. Branded lookup is attempted per item and matched items are combined.
Validation -> Missing/unmatched items add uncertainty notes. More than 8 items are listed as missing labels and not looked up.
API -> Internal `branded_food_lookup.lookup`.
Success -> Returns a combined estimate whose source is either a single source or `mixed_lookup`, with `portion_basis` recording the lookup queries.
Failure -> Falls back to top-level branded lookup or model/manual estimate.

### Label OCR

Trigger -> LM Studio returns `label_ocr=true` and `macro_estimate`.
Behavior -> The backend trusts printed nutrition label values more than image-based portion inference, skips branded lookup, sets source `vision_<provider>_label_ocr`, and caps confidence at 0.9.
Validation -> The macro estimate still goes through `sanitize_meal_estimate`.
API -> No separate endpoint.
Success -> Review card shows label-derived values and retention metadata.
Failure -> Invalid/incomplete label OCR falls back to manual review estimate with low confidence.

### Review / Edit / Accept

Trigger -> User edits review cards and taps save/log.
Behavior -> Review mutations call `/api/meal-intake/<meal_id>/refresh`; final acceptance calls `/api/meal-intake/<client_id>/accept`.
Validation -> Refresh kind must be one of the supported review actions. `add_item`, `edit_portion`, `choose_candidate`, and `followup_answer` require `request_id` and are idempotent for same request id/kind. Accept blocks if any included item is still blocked.
API -> `POST /api/meal-intake/<meal_id>/refresh`, `POST /api/meal-intake/<client_id>/accept`.
Success -> Accepted included items become `food_logs` with `correction_state=accepted` or `corrected`; skipped/deleted items are recorded as negative feedback; pending snapshot and pending row are cleaned up; workout adaptation is enqueued.
Failure -> Invalid fields return 400; missing snapshot/item/candidate returns 404; blocked review or mismatched request id returns 409.

### Discard / Undo

Trigger -> User discards pending estimate or deletes a logged meal.
Behavior -> Frontend calls `DELETE /api/meal-intake/<client_id>`, optionally with `correction_state=pending_review`.
Validation -> `client_id` must be non-empty and <= 128 chars. If expected correction state is provided, any mismatch returns 409.
API -> `DELETE /api/meal-intake/<client_id>`.
Success -> Food log row, meal review snapshot, multi-item rows, acceptance event, and legacy nutrition row are deleted if present.
Failure -> Returns `not_found` if nothing was removed.

### Offline Save and Replay

Trigger -> User submits while `navigator.onLine === false`.
Behavior -> The composer stores metadata in IndexedDB store `queued_meals` and photo `Blob`s in store `meal_photos`. It clears the active composer and shows a queued/sync message.
Validation -> Requires cached auth scope; does not silently drop draft on IndexedDB failure.
API -> No server call until reconnect.
Success -> On reconnect, queued entries are posted to `/api/meal-intake` with synthetic file names such as `meal-1.jpg`. Accepted server response causes the queued meal and photo blobs to be deleted.
Failure -> 5xx/network remains `pending`; 401/403 becomes `auth_required`; 409 becomes `conflicted`; other 4xx becomes `rejected`. Discard deletes metadata and photo blobs.

## 5. API Endpoints

| Method | Path | Auth | Trigger | Key params | Response shape | Real/Mock |
|---|---|---|---|---|---|---|
| POST | `/api/meal-intake` | Session + CSRF/same-origin | Submit text/photo meal | Multipart fields above | V2 pending-review payload, or error | Real |
| GET | `/api/meal-intake/pending` | Session | Page load/reload hydration | none | `{pending, pending_count, ttl_days, stale_removed}` | Real |
| POST | `/api/meal-intake/<meal_id>/refresh` | Session + CSRF/same-origin | Review-card mutation | `kind`, optional `item_id`, `text`, `candidate_id`, `meal_type`, `request_id` | Updated review payload | Real |
| DELETE | `/api/meal-intake/<client_id>` | Session + CSRF/same-origin | Discard pending/logged meal | optional `correction_state`/`state` query | `{status, removed}` or conflict | Real |
| POST | `/api/meal-intake/<client_id>/accept` | Session + CSRF/same-origin | Accept review | Legacy `estimate` body or V2 `items[]` body | `{status:"logged", food_log, photo_retention}` or multi-response | Real |
| GET | `/api/nutrition-today` | Session | Macro-card refresh after accept/discard | none | Daily nutrition totals/targets | Real consuming endpoint |
| GET | `/api/nutrition-history` | Session | Body/history trend views | range/date args [TBC] | Per-day rollups with pending/corrected/estimated context | Real consuming endpoint |
| GET | `/api/food-logs/by-date/<date>` | Session | Food log sheet/detail | `YYYY-MM-DD` date | Food log rows for date | Real consuming endpoint |

Non-obvious behavior:

- `POST /api/meal-intake` rejects non-multipart/non-form content with 415.
- Content-Length over 18 MB is rejected before parsing multipart.
- Raw image bytes are read into memory for the request and not persisted server-side.
- Fresh capture always returns pending review in current code, regardless of high confidence. The policy is still computed and returned for explanation.
- The former frontend mock URL has been retired from the production bundle. Browser QA must use explicit test fixtures with an isolated `DATA_DIR`; meal-composer submissions call the real API and can persist inside the selected data directory.

## 6. Data Model & Persistence

### SQLite: `fitness_data.db`

`data_store.DATA_DB` points to `runtime_config.data_path("fitness_data.db")`, so location depends on the app `DATA_DIR` runtime configuration.

`food_logs` stores durable food entries:

| Column | Meaning |
|---|---|
| `user_id` | Owner/profile id. |
| `client_id` | Idempotency key; unique with `user_id`. |
| `date` | Browser-local meal date used for daily nutrition bucketing. |
| `logged_at`, `source_timestamp` | Browser-local timestamp when available; server fallback otherwise. |
| `meal_type`, `item_name`, `portion_description`, `context_note` | User-visible meal identity/context. |
| `calories`, `protein_g`, `carbs_g`, `fat_g`, `sodium_mg`, `fiber_g` | Nutrition values. |
| `confidence`, `source`, `correction_state` | Estimate trust/provenance and whether the row counts. Pending review rows are excluded from totals/coaching context. |
| `original_estimate_json` | Sanitized estimate snapshot only; no raw images, prompts, traces, or paths. |
| `meal_id`, `meal_item_id`, `item_index`, `item_state` | Multi-item review linkage. |
| `vocab_learned_at` | Prevents repeat personal-vocabulary learning. |
| `created_at`, `updated_at` | Store timestamps. |

`meal_review_snapshots` stores pending review UI state by `(user_id, meal_id)`: `payload_json`, `next_item_seq`, `applied_refreshes_json`, timestamps.

`meal_acceptance_events` stores parent-meal terminal state and included/skipped/deleted fingerprinting so accept is idempotent and conflicts are detectable.

Related stores include `branded_lookup_cache`, `barcode_lookup_cache`, `personal_vocab`, `food_log_refresh_events`, and `workout_adaptation_pending/events`.

### Legacy JSON

Legacy `NUTRITION_DATA`/`NUTRITION_FILE` remains readable and is updated/deleted in some compatibility paths. Current accepted food logs use SQLite as the canonical path.

### Browser Storage

| Store | Contents | Retention |
|---|---|---|
| `localStorage` key `fit60_meal_draft` | Text draft and boolean `has_image`; no raw image bytes | Until cleared on success or empty draft. |
| IndexedDB `fitMealIntakeQueueDB.queued_meals` | Offline meal metadata and safe photo metadata | Until sync success or user discard. |
| IndexedDB `fitMealIntakeQueueDB.meal_photos` | Temporary raw photo `Blob`s for offline replay only | Deleted immediately after accepted sync or discard. |

## 7. Enums & Constants

### Provider Selection

| Value | Meaning | Real/Experimental |
|---|---|---|
| `lm_studio` | Default local provider. Uses LM Studio OpenAI-compatible chat completions with strict JSON schema. | Real/promoted |
| `ollama` | Local Ollama chat endpoint using base64 `images` and JSON output. | Real adapter, experimental fallback |
| `claude` | Anthropic Messages API image adapter using `ANTHROPIC_API_KEY`. | Real cloud adapter, not default |
| `disabled` | Explicitly disables vision and raises an error. | Real configuration |

Unsupported provider strings raise `unsupported vision provider: <value>`.

### LM Studio Vision Candidate Order

1. `primary`: `VISION_LM_STUDIO_URL` or `LM_STUDIO_URL`, model `VISION_LM_STUDIO_MODEL` or `LM_STUDIO_VISION_MODEL` or `qwen3-vl-30b-a3b-instruct@q4_k_xl`.
2. `low_memory`: same primary URL, model from `VISION_LM_STUDIO_LOW_MEMORY_MODEL`; only included when explicitly set and distinct from primary.
3. `fallback`: `VISION_LM_STUDIO_FALLBACK_URL` + `VISION_LM_STUDIO_FALLBACK_MODEL`; only included when both are set and distinct from primary.

### Meal Types

| Value | Meaning |
|---|---|
| `breakfast` | Morning meal. |
| `lunch` | Midday meal. |
| `dinner` | Evening meal. |
| `snack` | Default/fallback meal type and non-meal snack bucket. |

### Confidence Bands and Policy Reasons

| Constant/value | Meaning |
|---|---|
| High threshold `0.75` | Estimate must meet/exceed this and have no ambiguity/plausibility reasons to be policy-eligible for auto-log. |
| Medium threshold `0.55` | Medium confidence still goes to review with gentler copy. |
| `high`, `medium`, `low` | Confidence bands. |
| `low_confidence` | Confidence below 0.55. |
| `medium_confidence` | Confidence >= 0.55 and < 0.75. |
| `ambiguous_input` | Provider/parser marked estimate ambiguous. |
| `implausible_calories` | Calories missing/negative/outside bound. |
| `implausible_macros` | One macro/fiber value negative or > 500 g. |
| `implausible_sodium` | Sodium negative or > 12000 mg. |
| `missing_calories` | Calories absent/malformed. |

### Review State

| Value | Meaning |
|---|---|
| `pending_review` | Estimate is saved but excluded from nutrition totals/coaching until accepted. |
| `accepted` | User accepted estimate without material correction. |
| `corrected` | User changed accepted estimate relative to original. |
| `included` | V2 item should be logged. |
| `skipped` | V2 item intentionally skipped and recorded as negative feedback. |
| `deleted` | V2 item deleted and recorded as negative feedback. |

### Refresh Kinds

`add_item`, `edit_portion`, `followup_answer`, `choose_candidate`, `skip_item`, `delete_item`, `restore_item`, `set_meal_type`.

`add_item`, `edit_portion`, `choose_candidate`, and `followup_answer` require `request_id` for idempotency.

### Limits and Timeouts

| Constant | Value | Meaning |
|---|---:|---|
| Max photos per meal | 4 | Client and server cap. |
| Per-photo max bytes | 6 MB | Server and client cap. |
| Aggregate photo/request cap | 18 MB | Server/client cap including Content-Length precheck. |
| Pending review TTL | 7 days | Older pending rows are cleaned up on hydration. |
| LM Studio request timeout | 25 seconds | `VISION_LOCAL_TIMEOUT_SEC` default. |
| LM Studio preflight timeout | 1.5 seconds | `VISION_LM_STUDIO_PREFLIGHT_TIMEOUT_SEC`/`LM_STUDIO_PREFLIGHT_TIMEOUT_SEC` default. |
| LM Studio load retry limit | 2 | Adds up to 3 attempts for transient load errors. |
| LM Studio load retry backoff | 1.0 second | Multiplied by attempt number. |
| LM Studio warmup timeout | 45 seconds | Warmup request timeout. A cold LM Studio model can make the meal submit wait on in-request warmup before the real vision call. |
| LM Studio schema retry limit | 2 | Retries invalid JSON/schema twice per candidate. |
| Multi-image max dimension | 1024 px | Downscale target when `sips` exists and multiple images are sent. |
| Multi-image JPEG quality | 80 | Downscale output quality. |
| Vision temperature | 0.1 default | `VISION_TEMPERATURE`; invalid values fallback to default. |
| Claude timeout | 12 seconds | Hardcoded default in Claude adapter. |
| Ollama timeout | 25 seconds | Shared local adapter timeout. |

## 8. Integration Points

- Nutrition totals: accepted/corrected rows feed `/api/nutrition-today`, nutrition history, macro cards, and body-recomposition context.
- Workout adaptations: accepting food logs calls `_enqueue_workout_adaptation_after_accept`; pending rows do not drive plan changes.
- Branded lookup: vision descriptions and structured items can be matched to verified/known nutrition sources.
- Personal vocabulary: accept/correct/skip/delete feedback updates phrase learning after claim gating.
- AI coach health: Settings surfaces LM Studio primary/fallback health for the broader AI coach, but not as a direct health endpoint for vision.
- Backup/export: food log rows and sanitized estimates may be exported; raw photo bytes are excluded.
- Offline queue: uses `/api/auth/scope` to bind queued meals to the signed-in account before sync.

## 9. Permissions & Security

All meal-intake routes are session-protected by the global auth guard. Browser state-changing requests require the app's CSRF/same-origin protection: `X-Requested-With: XMLHttpRequest`, valid form CSRF, or same-origin browser metadata; cross-origin browser headers are rejected.

Uploaded image bytes are not echoed in responses, not stored in `food_logs`, not included in backups, and not written to localStorage. Temporary server-side downscaling for multi-image LM Studio requests uses a temporary directory and deletes it when the operation exits. Offline raw photo blobs are stored only in IndexedDB and are tied to a local auth scope.

The Claude provider sends food images to Anthropic when explicitly selected and `ANTHROPIC_API_KEY` is configured. That is a real external integration and does not meet the local-first privacy profile of the default LM Studio route.

## 10. Business Rules

- Photo-only and text-only submissions both route to review before counting in current implementation.
- A photo estimate with valid label OCR can use printed nutrition values but still requires review.
- If a photo fails and text is present, text fallback keeps the meal flow alive and records `vision_error`.
- If a photo fails and no text is present, the user must add a meal description and retry.
- Lookup confidence is capped by vision confidence when a verified/branded lookup is combined with a vision result.
- Non-label-ocr macro estimates from vision are capped at 0.65 confidence; label OCR estimates are capped at 0.9.
- Manual/failed estimates default to 0 calories/macros, low confidence, `ambiguous=true`, and pending review.
- Multi-photo prompts treat all photos as one meal and explicitly instruct the model not to double-count repeated items.
- Pending review rows are excluded from daily totals and coaching context.
- Browser-local date fields take precedence over server time for meal bucketing.
- Retried pending entries preserve the original local meal date/time to avoid cross-midnight relocation.
- Retry of an in-composer transient failure reuses the same `client_id`; retry of a pending review uses a new `client_id` and then deletes the old pending row.
- Structured item lookup uses at most 8 items even if the provider returns up to 10 cleaned items.

## 11. Config & Environment

| Variable | Default | Meaning |
|---|---|---|
| `VISION_ESTIMATOR_PROVIDER` | `lm_studio` | Selects `lm_studio`, `ollama`, `claude`, or `disabled`. |
| `VISION_LM_STUDIO_URL` | falls back to `LM_STUDIO_URL`, then `http://127.0.0.1:1234` | Primary LM Studio vision endpoint. |
| `LM_STUDIO_URL` | `http://127.0.0.1:1234` | Backward-compatible primary endpoint fallback. |
| `VISION_LM_STUDIO_MODEL` | falls back to `LM_STUDIO_VISION_MODEL`, then served 30B model | Primary vision model. |
| `LM_STUDIO_VISION_MODEL` | none | Backward-compatible vision model name. |
| `VISION_LM_STUDIO_LOW_MEMORY_MODEL` | empty | Optional same-host lower-memory candidate. Empty means no low-memory fallback. |
| `VISION_LM_STUDIO_FALLBACK_URL` | empty | Optional fallback LM Studio URL, normally Mac Studio per product docs. |
| `VISION_LM_STUDIO_FALLBACK_MODEL` | empty | Required with fallback URL to include fallback candidate. |
| `VISION_LOCAL_TIMEOUT_SEC` | `25` | Request timeout for local vision calls. |
| `VISION_LM_STUDIO_PREFLIGHT_TIMEOUT_SEC` / `LM_STUDIO_PREFLIGHT_TIMEOUT_SEC` | `1.5` | Model-list preflight timeout. |
| `VISION_LM_STUDIO_LOAD_RETRY_LIMIT` | `2` | Retry count for transient LM Studio load errors. |
| `VISION_LM_STUDIO_LOAD_RETRY_BACKOFF_SEC` | `1.0` | Backoff multiplier. |
| `VISION_LM_STUDIO_WARMUP_TIMEOUT_SEC` | `45` | Warmup request timeout. |
| `VISION_LM_STUDIO_MULTI_IMAGE_MAX_DIMENSION` | `1024` | Multi-image downscale max dimension; <=0 disables downscale. |
| `VISION_LM_STUDIO_MULTI_IMAGE_JPEG_QUALITY` | `80` | Downscale JPEG quality. |
| `VISION_TEMPERATURE` | `0.1` | LM Studio vision temperature. |
| `VISION_LM_STUDIO_KEEP_WARM` | off | If truthy and provider is `lm_studio`, direct `python app.py` startup launches a one-shot daemon keep-warm thread; the call sits inside the `__main__` block and never runs under the production gunicorn entrypoint (see FIT-258). |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama endpoint. |
| `VISION_OLLAMA_MODEL` / `OLLAMA_VISION_MODEL` | `llava:latest` | Ollama vision model. |
| `ANTHROPIC_API_KEY` | unset | Required for Claude adapter. |
| `CLAUDE_VISION_MODEL` | `claude-sonnet-4-5` | Claude vision model name. |

## 12. Test Coverage

Existing tests and evidence:

- `tests/test_fit221_vision_keep_warm.py` covers keep-warm candidate iteration, sanitized failure reporting, single-flight locking, preflight-warmed behavior, and env/provider-gated daemon startup.
- `tests/test_vision_estimator.py` appears in graph results and covers provider defaults, LM Studio posting/parsing, schema retries, fallback candidates, low-memory opt-in, transient load errors, OOM sanitization, raw-input non-leakage, multi-image payload/downscale behavior, Ollama multi-image payloads, Claude env requirement, and label OCR preservation.
- `tests/test_meal_intake_api.py`, `tests/test_meal_logging_e2e.py`, and related meal tests appear in graph results for legacy image key, plural images, photo-only submit, pending review, and privacy behavior.
- `tests/test_meal_model_benchmark.py` covers benchmark payload/schema behavior.
- `docs/MEAL_MODEL_DECISION.md` and benchmark docs provide product evidence for model choice, not runtime test assertions.

Coverage gaps:

- `VISION_LM_STUDIO_KEEP_WARM` does not run under the production gunicorn entrypoint today; FIT-258 tracks keep-warm under gunicorn plus inference lock/request deadline.
- [TBC] No endpoint exposes vision-specific health/preflight status to the meal composer, so the UI cannot distinguish "model loading" from generic estimator failure before submit.
- [TBC] Broad live mobile QA for camera capture, multi-photo retry, and offline photo replay was not run for this PRD due to the no-run constraint.

## 13. Gaps & Issue Candidates

### IC-1: Wire production-safe vision keep-warm and request deadlines
- **Type:** Improvement
- **Priority:** high
- **Where:** `app.py:318`, `app.py:16057`, `local_vision_adapter.py:107`
- **Problem:** Keep-warm is opt-in and tested as a daemon thread from `app.py`, but the assigned sources do not prove that it runs reliably under the production gunicorn/worker topology or that request deadlines are enforced end-to-end for contended model loads.
- **Why it matters:** The owner sees photo logging as slow or failed when the VLM is cold or stuck loading, even though the deterministic app remains healthy.
- **Acceptance criteria:**
  - Production runtime starts at most one keep-warm job per intended worker/process.
  - Meal-intake vision requests have a hard deadline that returns a reviewable fallback/error.
  - Logs expose sanitized candidate role/model/status without raw image data.
  - Focused tests cover production startup path and timeout behavior.
- **Duplicate-of:** FIT-258

### IC-2: Add a vision health/preflight status endpoint for Settings and Log
- **Type:** Feature
- **Priority:** medium
- **Where:** `vision_estimator.py`, `local_vision_adapter.py:285`, `templates/index.html:157`
- **Problem:** Settings shows general AI coach health, but the meal composer has no vision-specific readiness indicator. A user only discovers missing VLM config, unloaded model, or fallback routing after submitting a meal.
- **Why it matters:** Photo logging is a mobile capture workflow; preflight clarity prevents wasted uploads and confusing failures.
- **Acceptance criteria:**
  - Authenticated endpoint returns configured provider, candidate roles, reachable/model-loaded status, and sanitized errors.
  - Meal composer shows "ready", "warming", "fallback", or "unavailable" without exposing secrets or URLs unless intended.
  - Status does not send real food images and does not mutate meal state.
- **Duplicate-of:** none

### IC-3: Preserve photo provenance on canonical food-log reads
- **Type:** Data-contract
- **Priority:** medium
- **Where:** `data_store.py:62`, `app.py:6005`, `app.py:6328`
- **Problem:** The write path marks `from_image`, `vision_description`, `vision_provider`, and `vision_confidence`, but issue history already identifies gaps around photo provenance in canonical food-log reads and meal detail.
- **Why it matters:** The user needs to understand which logged nutrition values came from photo estimates versus text/manual sources after the review card is gone.
- **Acceptance criteria:**
  - Food-log list/detail APIs consistently include sanitized photo provenance fields.
  - Meal detail shows provider/confidence/retention context for photo-originated entries.
  - Backup/export continues to exclude raw photos and raw traces.
- **Duplicate-of:** FIT-229

### IC-4: Consolidate food pipeline robustness around sodium and dedup
- **Type:** Improvement
- **Priority:** medium
- **Where:** `app.py:6020`, `meal_estimate_schema.py:51`, `data_store.py:26`
- **Problem:** The pipeline has multiple nutrition sources and fallback paths; open issue FIT-266 already tracks robustness concerns such as logging, sodium-zero behavior, USDA candidate loop, and dedup consolidation.
- **Why it matters:** Photo estimates are trusted only if provenance, sodium defaults, and duplicate/candidate behavior are consistent across model, lookup, and manual fallback paths.
- **Acceptance criteria:**
  - Sodium `0` is distinguishable from unknown when source data does not provide sodium.
  - Duplicate candidate and lookup paths are consolidated or documented.
  - Logs capture sanitized reason/source transitions for failed lookup and fallback.
  - Regression tests cover image-plus-lookup and image-without-lookup paths.
- **Duplicate-of:** FIT-266

### IC-5: Surface provider fallback details in the review card
- **Type:** Improvement
- **Priority:** low
- **Where:** `app.py:7434`, `local_vision_adapter.py:270`, `static/js/app.js:11260`
- **Problem:** LM Studio records `_meta` with candidate role/model/fallback-used internally, but the public response only exposes provider and confidence. The user cannot tell whether the estimate came from primary, low-memory, or fallback hardware.
- **Why it matters:** Hardware fallback affects latency and trust during local model operation; surfacing a safe label helps debug without reading logs.
- **Acceptance criteria:**
  - Public response includes safe candidate role such as `primary`, `low_memory`, or `fallback`.
  - UI shows a compact "local vision: fallback" indicator only when non-primary was used.
  - No raw URL, prompt, image, or private model trace is exposed.
- **Duplicate-of:** none

### IC-6: Add live mobile QA for multi-photo and offline replay states
- **Type:** Test
- **Priority:** medium
- **Where:** `static/js/app.js:9577`, `static/js/app.js:7824`, `templates/index.html:157`
- **Problem:** The code implements multi-photo capture, thumbnail removal, offline IndexedDB storage, auth-scope gating, and retry cleanup, but this PRD pass did not find live mobile QA proof for those interactive states in the assigned evidence.
- **Why it matters:** Photo food logging is primarily mobile; broken file-picker, preview, or offline cleanup behavior can silently create trust and privacy failures.
- **Acceptance criteria:**
  - QA covers iOS/Android or equivalent mobile browser capture/upload behavior.
  - QA covers four-photo cap, over-size errors, thumbnail removal, offline queue, reconnect replay, and discard cleanup.
  - Proof confirms raw photo blobs are removed after sync/discard.
- **Duplicate-of:** none
