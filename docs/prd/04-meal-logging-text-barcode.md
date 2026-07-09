# Meal Logging Text & Barcode — PRD

> **Sources:** `README.md`, `docs/VISION.md`, `docs/PRD.md`, `docs/CURRENT_STATE.md`, `docs/MEAL_INTAKE_CONTRACT.md`, `docs/MEAL_MODEL_DECISION.md`, `app.py`, `meal_text_parser.py`, `meal_estimate_schema.py`, `meal_log_policy.py`, `personal_vocab.py`, `data_store.py`, `templates/index.html`, `static/js/app.js`, `tests/test_food_log_api.py`, `tests/test_fit134_review_ui.py`, `tests/test_fit142_barcode_ui.py`, `tests/test_fit145_offline_queue.py`, `tests/test_fit150_pending_refresh.py`, `tests/test_fit210_manual_review_badge.py`, `tests/test_fit219_review_card_a11y.py`, `tests/test_backup_import_food_logs.py`, `tests/test_meal_intake_review_contract.py`, `tests/test_meal_logging_e2e.py`
> **Routes:** `POST /api/meal-intake`, `POST /api/meal-intake/barcode`, `GET /api/meal-intake/pending`, `POST /api/meal-intake/<meal_id>/refresh`, `DELETE /api/meal-intake/<client_id>`, `POST /api/meal-intake/<client_id>/accept`, `GET /api/nutrition-today`, `GET /api/nutrition-history`, `GET /api/food-logs/by-date/<date>`, `GET /api/food-log-refresh-events`, `POST /api/food-log-refresh-events/<event_id>/ack`, `POST /api/add-nutrition`, `GET /api/personal-vocab`, `DELETE /api/personal-vocab/<path:normalized_input>`, `GET /api/export-backup`, `POST /api/import-backup`
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

## 1. Overview

Meal logging lets the owner record food quickly from free text or a packaged-food barcode, review the app's nutrition estimate, and save accepted items into the canonical food log. The feature exists because workout readiness and recovery decisions depend on current fuel, protein, sodium, and late-meal context, but the app deliberately avoids counting uncertain nutrition until the owner reviews it.

The normal text flow is: type a meal, submit, estimate, review card, edit or clarify, save, then the accepted food log feeds dashboard calories/macros and workout adaptation. Barcode follows the same review-first lifecycle. A verified barcode source creates a provider-backed estimate; an unknown barcode creates a persistent manual-review card when the UI retries with `allow_pending: true`.

The live review surface is V2 multi-item meal review when the backend returns `meal_id` plus `items[]`. The older single-item review card still exists for legacy response shapes and tests. Photo capture and vision estimation plug into the same intake contract but belong to PRD 05; this PRD documents only where photo-derived estimates enter the text/barcode review and food-log lifecycle. Branded, USDA, Open Food Facts, Nutritionix, and H-E-B lookup internals belong to [06 Nutrition Data Sources](06-nutrition-data-sources.md).

## 2. User-Facing Surfaces

| Surface | Location | Behavior |
| --- | --- | --- |
| Meal composer | Dashboard macro/nutrition area in `templates/index.html`; wired by `static/js/app.js` | Text box capped at 500 characters, optional image attachment, barcode scan button, submit button, offline banner, transient error/status region, photo retention note, and pending-review list. |
| Barcode panel | Inline panel inside the meal composer | Opens camera scanner when `BarcodeDetector` and `getUserMedia` are available; otherwise shows manual barcode input. Sends only decoded barcode digits, not camera frames. |
| V2 review card | `static/js/app.js` `buildMealReviewCardV2` | Collapsed view shows meal type, total calories, macro summary, manual-review badge when applicable, Save/Discard actions, and expand/collapse. Expanded view shows follow-up question, item list, top candidates, source chip/viewer, edit portion, skip, delete, restore, and add item. |
| Legacy single-item review card | `static/js/app.js` `buildMealPendingRow` | Shows one estimate with confidence, source, policy reason chips, editable fields, portion multiplier chips, Retry, Discard, and Accept. This path remains wired for non-V2 payloads. |
| Food log modal/detail | `templates/index.html` and `static/js/app.js` | Shows Today, Yesterday, and recent 14-day entries; meal detail supports edit/save/cancel and delete. Food-log reads use `/api/food-logs/by-date/<date>`. |
| Sync queue modal/banner | `templates/index.html` and `static/js/app.js` | Shows offline meals and workouts saved on the device, retry/discard controls, and privacy copy that discard clears offline meal photos. |
| Macro card and nutrition history | Dashboard macro card and history charts | Accepted food logs update calories, protein, carbs, fat, sodium, warning chips, next-day context, and 14-day history. Pending review rows are counted separately and excluded from totals. |
| Personal vocabulary management | API-only plus backup/export today | Learned phrase mappings are stored and backed up. A dedicated visible management panel was not found in the assigned UI sources. [TBC] Whether another settings surface exposes it. |

## 3. Field Inventory

### Meal Composer Submission

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `client_id` | string | Yes | Generated by browser UUID fallback | Max 128 chars | Idempotency key. Retries with the same client ID must not create duplicate meals. |
| `text` | string | No, unless no images | Empty | Trimmed; max 500 chars | Owner's meal description or extra context for a photo. |
| `images` | file list | No, unless no text | Empty | Max 4 files, each non-empty image with MIME `image/jpeg`, `image/png`, `image/webp`, or `image/gif`; max 6 MB each; aggregate max 18 MB | Photo context. Photos are validated and discarded after estimation on the server. |
| `image` | file | No | Empty | Legacy singular image key; same file rules | Back-compat path for older clients. |
| `local_timestamp` | string | No | Browser current ISO timestamp or server fallback | Max 64 chars | Original browser timestamp for `logged_at`, `source_timestamp`, and retry preservation. |
| `local_date` | string | No | Browser current local date or derived fallback | Max 10 chars | Local calendar day for date bucketing. |
| `local_iso` | string | No | Browser current local ISO with offset | Max 64 chars | Preferred timestamp for `logged_at` derivation; date fallback when `local_date` is absent. |

### Barcode Submission

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `client_id` | string | Yes | Generated by browser | Max 128 chars | Idempotency key for barcode lookup/retry. |
| `barcode` | string | Yes | None | Whitespace, dots, underscores, and hyphens removed; normalized value must be 8, 12, 13, or 14 digits | UPC/EAN/GTIN package identifier. |
| `allow_pending` | boolean | No | `false` | Must be a JSON boolean when supplied | If `true`, an unresolved barcode becomes a manual-review card instead of a 404. |
| `local_timestamp` | string | No | Browser now | Max 64 chars | Same meaning as text intake. |
| `local_date` | string | No | Browser today | Max 10 chars | Same meaning as text intake. |
| `local_iso` | string | No | Browser local ISO with offset | Max 64 chars | Same meaning as text intake. |

### Public Meal Estimate Schema

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `item_name` | string | Yes | None | Required non-empty string; UI single-item edit max 160 | Human-readable food name. |
| `portion_description` | string/null | Yes under schema defaults | `null` | String or null; UI single-item edit max 240 | Serving/portion description shown for review. |
| `meal_type` | enum | Yes under schema defaults | `snack` | `breakfast`, `lunch`, `dinner`, `snack` | Calendar meal bucket; editable on review. |
| `calories` | number | Yes | None; fallback can be 0 for manual review | Non-bool numeric; non-negative; plausible max 5000 when plausible-range validation is enabled; rounded to integer | Energy count used in nutrition totals after acceptance. |
| `protein_g` | number | Yes | None or 0 in manual review | Non-bool numeric; non-negative; plausible max 500 g; rounded to 0.1 g | Protein total. |
| `carbs_g` | number | Yes | None or 0 in manual review | Non-bool numeric; non-negative; plausible max 500 g; rounded to 0.1 g | Carbohydrate total. |
| `fat_g` | number | Yes | None or 0 in manual review | Non-bool numeric; non-negative; plausible max 500 g; rounded to 0.1 g | Fat total. |
| `sodium_mg` | number | Yes under schema defaults | 0 | Non-bool numeric; non-negative; plausible max 12000 mg; rounded to integer | Sodium total and next-day context input. |
| `fiber_g` | number | Yes under schema defaults | 0 | Non-bool numeric; non-negative; plausible max 500 g; rounded to 0.1 g | Fiber total. |
| `confidence` | number | Yes under schema defaults | 0 | 0.0 through 1.0; rounded to 0.01 | Estimate confidence and review policy input. |
| `ambiguous` | boolean | Yes under schema defaults | `true` | Boolean only | Whether identity, portion, or source is unclear. |
| `uncertainty_notes` | string array | Yes under schema defaults | `[]` | Strings only; unknown model fields dropped | User-facing reasons or caveats. |
| `source` | string | Yes | None; fallback/manual sources set explicitly | Required string | Provenance/source tag used for UI labels, trust, and cache behavior. |
| `external_food_id` | string | No | Omitted | Preserved only as safe provenance | Provider item ID, UPC, FDC ID, Nutritionix ID, OFF code, or H-E-B product ID. |
| `verified_source_url` | URL string | No | Omitted | Safe provenance only | Provider/product source link. |
| `data_fetched_at` | ISO string | No | Current fetch time when provider-backed | Safe provenance only | When provider nutrition was retrieved. |
| `portion_basis` | string | No | Omitted | Safe provenance only | Explains serving basis such as Nutritionix serving, USDA 100 g, OFF serving, or manual pending. |
| `brand_id` | string | No | Omitted | Safe provenance only | Internal brand/provider identifier for cache trust. |
| `underlying_source` | string | No | Omitted | Safe provenance only | Original provider when `source` is `local_cache` or composed. |
| `off_attribution` | string/object | No | Omitted | Safe provenance only | Open Food Facts attribution text/object where present. |
| `personal_vocab_phrase` | string | No | Omitted | Safe provenance only | Phrase that matched learned personal vocabulary. |
| `vision_description`, `vision_provider`, `vision_confidence`, `fallback_reason` | strings/numbers | No | Omitted | Safe metadata allowlist | Photo/vision and fallback provenance; photo estimation belongs to PRD 05. |

### V2 Review Payload

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `meal_id` | string | Yes | Generated server-side | Non-empty; refresh route max 128 | Stable ID for the review meal, snapshot, accept, and discard. |
| `meal_type` | enum | Yes | `snack` if unsupported | `breakfast`, `lunch`, `dinner`, `snack` | Meal bucket applied to included items on save. |
| `meal_totals` | object | Yes | Recomputed from included items | Numeric `calories`, `protein_g`, `carbs_g`, `fat_g`, `sodium_mg`, `fiber_g` | Collapsed review-card summary. |
| `items[]` | array | Yes | Empty not useful | Each item has stable `item_id` and status | Reviewable food items inside a meal. |
| `items[].status` | enum | Yes | `included` | `included`, `skipped`, `deleted` | Included items are saved; skipped/deleted remain for negative feedback and undo. |
| `items[].estimate` | object | Yes for accept | Current sanitized estimate | Same estimate schema; server revalidates | Source of canonical food log fields. |
| `items[].original_estimate` | object/null | No | Current estimate | Sanitized when present | Used to decide whether accepted row was corrected. |
| `items[].source` | object | No | UI falls back to manual label | `kind`, `label`, optional same-origin link | Review source chip and source viewer. |
| `items[].candidates[]` | array | No | `[]` | UI shows up to 3 | Alternate matches the user can choose. |
| `items[].unclear` | boolean | No | Falsey | Server uses for blocking | Marks item needing clarification. |
| `save_blocked_item_ids[]` | string array | Yes | `[]` | Included unresolved item IDs | Save is blocked until each listed item is clarified, skipped, deleted, or resolved. |
| `followup` | object | No | `{available:false, question:null, used:false}` | `available`, `question`, `used` | One follow-up question path for unclear items. |

### Food Log Entry

| Field | Type | Required | Default | Validation | Business meaning |
| --- | --- | --- | --- | --- | --- |
| `client_id` | string | Yes for idempotent writes | Generated `nutrition-...` for manual legacy writes or `backup-food-log-...` for legacy backup imports | Unique per user in SQLite | Stable entry identifier. |
| `date` | string | Yes | Local date or today | App expects `YYYY-MM-DD` in read route; add route accepts max 32 | Calendar bucket. |
| `logged_at` | string | No | Now | Stored as text | Display ordering and detail timestamp. |
| `source_timestamp` | string | No | Local timestamp | Stored as text | Original source timestamp for imported/synced entries. |
| `meal_type` | enum/string | No | `snack` for estimates; null for some legacy entries | Estimate schema restricts enum; legacy add route max 64 | Meal bucket. |
| `item_name` | string | No | `Meal` or legacy blank | Max 160 in add route | Food label. |
| `portion_description` | string | No | Null | Max 240 in add route | Serving description. |
| `context_note` / `notes` | string | No | Null | Max 500 | Owner or system context. |
| Macro fields | numbers | Yes for calories/protein in manual add; optional for others | Sodium/fiber can default 0 in estimates | Non-negative; route-specific rounding | Dashboard totals. |
| `confidence` | number/null | No | Null or estimate value | Add route accepts 0..100; estimate path uses 0..1 | Estimate trust marker. [TBC] Legacy route allows 0..100 while estimate schema uses 0..1. |
| `source` | string | No | `manual`, `vision_estimate`, estimate source, or provider source | Max 64 in add route | Source label and trust/provenance. |
| `correction_state` | enum/string | No | `accepted` when estimate-backed, `manual` for manual add, `pending_review` while unresolved | Stored as text | Counts only accepted/corrected/manual states in totals; pending is excluded. |
| `original_estimate_json` | JSON text | No | Null | Sanitized on import/export | Original estimate/provenance for review and learning. |
| `meal_id`, `meal_item_id`, `item_index`, `item_state` | strings/int | No | Null | V2 accept/import fields | Connect canonical rows back to a multi-item meal. |
| `vocab_learned_at` | timestamp | No | Null | Set when vocabulary claim succeeds | Prevents duplicate personal-vocabulary learning. |

## 4. Interactions & Flows

### Text Meal Submission

Trigger → The owner types a meal and taps `Log`.

Behavior → The browser captures `client_id`, `local_timestamp`, `local_date`, and `local_iso`, disables the submit button, and posts `multipart/form-data` to `/api/meal-intake`. The backend rejects unsupported content types, missing text/image, excessive body size, invalid files, or missing client ID before parsing.

Validation → Text is capped at 500 characters. The parser first checks personal vocabulary, then eligible branded direct lookup, then LM Studio text estimation behind an inference lock, then deterministic fallback. Parser errors do not crash the flow; they return low-confidence fallback/manual estimates.

API → `POST /api/meal-intake`.

Success → Current backend behavior forces fresh submissions into pending review, creates/updates a pending `food_logs` row with `correction_state: pending_review`, saves a V2 review snapshot when applicable, and returns the review payload. The UI clears the composer, renders the pending card, and warns that review is required before the meal counts.

Failure → 4xx validation failures show an error and clear retry state. 5xx/network failures preserve the draft and client ID, expose an in-composer Retry button, and avoid duplicate rows by resubmitting with the same client ID.

### Barcode Scan and Lookup

Trigger → The owner taps Scan, allows camera access, or enters digits manually.

Behavior → The browser uses `BarcodeDetector` with formats `ean_13`, `ean_8`, `upc_a`, `upc_e`, and `code_128` where available, polls frames about every 450 ms, and stops the camera before sending the decoded barcode. If camera support or permission is unavailable, the panel stays usable with manual entry.

Validation → Barcode digits must normalize to 8, 12, 13, or 14 digits. Barcode lookup requires online server access; offline barcode capture is not queued.

API → The UI first posts `allow_pending: false` to `/api/meal-intake/barcode`. If the backend returns `404 barcode_not_found`, it retries once with the same `client_id` and `allow_pending: true`.

Success → Verified provider results become pending-review estimates with source metadata. Unknown barcodes become low-confidence manual-review V2 cards with source kind `barcode_pending_source`, a persistent `Manual review` badge, and editable fields before save.

Failure → A true `501` disables only barcode controls. A recoverable unresolved-barcode 404 leaves barcode controls enabled. Other failures show an error and clear the barcode draft ID only for non-5xx failures.

### V2 Review Refresh

Trigger → The owner adds an item, edits a portion, answers a follow-up, chooses a candidate, skips/deletes/restores an item, or changes meal type.

Behavior → The card sets `pendingRefresh`, disables edit controls while the refresh is in flight, and posts a mutation to `/api/meal-intake/<meal_id>/refresh`. Guarded mutation kinds carry a browser-generated `request_id` for idempotency.

Validation → `kind` must be one of the refresh enums. `add_item`, `edit_portion`, `choose_candidate`, and `followup_answer` require `request_id`; text fields are capped at 500 on the server and 240 in most UI inputs. Item actions require an existing item; portion edit requires an included item; candidate choice requires a known candidate.

API → `POST /api/meal-intake/<meal_id>/refresh`.

Success → The server recomputes item estimates, totals, blocked item IDs, follow-up state, and the pending food-log snapshot, then returns the full replacement review payload.

Failure → Duplicate same `request_id` and same kind is an idempotent replay. Duplicate same `request_id` with a different kind returns 409. Unknown meal returns 404. UI restores controls and shows a toast.

### Save, Discard, and Legacy Accept

Trigger → The owner taps Save/Accept or Discard.

Behavior → V2 Save sends `meal_id`, `meal_type`, and every item with its state, estimate, and original estimate. Included items are persisted as canonical `food_logs`; skipped/deleted items record negative personal-vocabulary feedback. Discard deletes the pending snapshot and pending rows. Legacy Accept sends one edited estimate under the URL `client_id`.

Validation → Save is blocked if `save_blocked_item_ids` is non-empty. Server also blocks included items that are unclear, have missing/low confidence below 0.55, missing/invalid calories, calories over 5000, macros/fiber over 500 g, or sodium over 12000 mg. If every item is skipped/deleted, the V2 UI replaces Save with Discard log.

API → `POST /api/meal-intake/<client_id>/accept` and `DELETE /api/meal-intake/<client_id>`.

Success → Accepted/corrected rows count in nutrition totals; pending snapshots are removed; workout adaptation is enqueued; personal vocabulary records accepted or negative feedback; the macro card refreshes.

Failure → Conflict checks protect pending delete when the stored `correction_state` no longer matches the requested state. Accept can return 409 blocked for unresolved V2 items.

### Pending Hydration and Expiry

Trigger → Dashboard load or refresh.

Behavior → The UI calls `/api/meal-intake/pending`, normalizes V2 snapshots through `normalizeMealV2Entry`, and renders pending cards. Legacy entries are normalized from `client_id` plus estimate.

Validation → Pending rows older than 7 days are treated as stale and removed during hydration, along with their review snapshots.

API → `GET /api/meal-intake/pending`.

Success → Response includes `pending`, `pending_count`, `ttl_days`, and `stale_removed`.

Failure → UI shows a toast and leaves current local pending state unchanged.

### Offline Meal Queue

Trigger → The owner submits text/photo while `navigator.onLine === false`.

Behavior → The browser stores metadata in IndexedDB object store `queued_meals` and photo blobs in `meal_photos`, keyed by `client_id`. It preserves original timestamps and auth scope, clears the composer, and shows that the meal will sync when reconnecting.

Validation → Offline save requires the same client-side file limits as online capture. Auth scope is checked before replay; if the current scope does not match the scope that saved the meal, the entry remains visible as `auth_required`.

API → Replay posts the same `FormData` contract to `/api/meal-intake`.

Success → Server-accepted queued meals are removed from IndexedDB; photo blobs are deleted; returned pending review payloads render with no local file handles.

Failure → 401/403 becomes `auth_required`, 409 becomes `conflicted`, 5xx/network stays `pending`, other 4xx becomes `rejected`, and accepted-but-local-delete failure becomes `eviction_failed`. Retryable statuses are `pending` and `auth_required`; flush runs serially on reconnect and boot.

### Nutrition Totals, History, and Food Log Reads

Trigger → Dashboard card load, save/discard, date selection, or history view.

Behavior → Accepted `food_logs` for a date are the source of truth when any accepted rows exist. If there are no accepted food-log rows for the day, the app falls back to legacy `NUTRITION_DATA`. Pending rows are excluded from totals but counted for review warnings.

Validation → `/api/food-logs/by-date/<date>` requires canonical `YYYY-MM-DD`. History covers the last 14 days, oldest to newest.

API → `GET /api/nutrition-today`, `GET /api/nutrition-history`, `GET /api/food-logs/by-date/<date>`.

Success → Totals include calories, protein, carbs, fat, sodium, targets, remaining amounts, percentages, accepted entry count, pending count, warnings, next-day context, and food-log rows.

Failure → Invalid date returns 400 `invalid_field`.

### Backup Import and Export

Trigger → The owner exports or imports a JSON backup.

Behavior → Export includes legacy `nutrition`, canonical `food_logs`, `meal_acceptance_events`, `meal_review_snapshots`, and `personal_vocab`. Import restores JSON-backed nutrition under a JSON lock, then replays food logs and meal/vocab tables into SQLite.

Validation → Import requires a top-level `data` object. Food-log import normalizes records through `_food_log_import_record` before `add_food_log`.

API → `GET /api/export-backup`, `POST /api/import-backup`.

Success → Imports are idempotent for existing `client_id` rows; legacy food logs without `client_id` receive generated `backup-food-log-...` IDs. Personal vocabulary and meal acceptance events round-trip.

Failure → Import returns 400 with the exception message on validation or restore failure.

## 5. API Endpoints

| Method | Path | Auth | Trigger | Key params | Response shape | Real/Mock |
| --- | --- | --- | --- | --- | --- | --- |
| POST | `/api/meal-intake` | Owner session/CSRF | Text/photo submit and offline replay | `client_id`, `text`, `images`, timestamps | V2 review payload or legacy `{status, estimate, food_log, policy}`; error object | Real |
| POST | `/api/meal-intake/barcode` | Owner session/CSRF | Barcode lookup | `client_id`, `barcode`, `allow_pending`, timestamps | Review payload plus `barcode`, `lookup_source`, `cache_hit`, `pending_source` | Real |
| GET | `/api/meal-intake/pending` | Owner session | Dashboard hydration | None | `{pending, pending_count, ttl_days, stale_removed}` | Real |
| POST | `/api/meal-intake/<meal_id>/refresh` | Owner session/CSRF | V2 card mutation | `kind`, optional `request_id`, item/text fields | Full replacement V2 review payload | Real |
| DELETE | `/api/meal-intake/<client_id>` | Owner session/CSRF | Discard pending or undo logged | Optional `correction_state`/`state` query | `{status, removed}` or conflict | Real |
| POST | `/api/meal-intake/<client_id>/accept` | Owner session/CSRF | Save reviewed meal | V2 `items[]` or legacy `estimate` body | `{status, food_logs}` or blocked/conflict | Real |
| GET | `/api/nutrition-today` | Owner session | Macro card | None | Public nutrition payload for today | Real |
| GET | `/api/nutrition-history` | Owner session | 14-day chart/log | None | 14 day objects with totals and breakdown | Real |
| GET | `/api/food-logs/by-date/<date>` | Owner session | Food log modal/detail | Date path | `{date, entries, count}` | Real |
| GET | `/api/food-log-refresh-events` | Owner session | Provider refresh notices | `unacknowledged`, `since`, `limit` | `{events}` | Real |
| POST | `/api/food-log-refresh-events/<event_id>/ack` | Owner session/CSRF | Acknowledge refresh event | Event ID | `{status, id}` | Real |
| POST | `/api/add-nutrition` | Owner session/CSRF | Manual food-log add/edit compatibility | Macro fields, item metadata, optional `client_id` | `{status:"success", nutrition, food_log}` | Real |
| GET | `/api/personal-vocab` | Owner session | Vocabulary inspection | None | List of entries. [TBC] Exact response wrapper shape not read. | Real |
| DELETE | `/api/personal-vocab/<path:normalized_input>` | Owner session/CSRF | Remove learned phrase | Path phrase, including slashes; max 500 chars | Delete result. [TBC] Exact response wrapper shape not read. | Real |
| GET | `/api/export-backup` | Owner session | Backup export | None | Downloaded JSON backup | Real |
| POST | `/api/import-backup` | Owner session/CSRF | Backup import | Backup JSON | `{status, message, imported}` | Real |

Non-obvious endpoint behavior:

- `POST /api/meal-intake` returns 415 for unsupported content type, 413 for aggregate payload too large, 400 for validation errors, and 503 when photo estimation fails with no text fallback.
- `POST /api/meal-intake/barcode` returns 415 for non-JSON, 413 for body too large, 400 for missing/invalid fields, and 404 `barcode_not_found` when `allow_pending` is false and no verified result exists.
- V2 refresh idempotency is per `request_id` and mutation kind. Replaying the same guarded kind returns the previous payload; reusing the ID with another kind conflicts.
- Delete is state-aware when `correction_state` is supplied; it refuses to remove a row that no longer has that state.
- `/api/food-log-refresh-events` clamps `limit` to 1..50 and defaults to unacknowledged events.

## 6. Data Model & Persistence

Runtime data is local under `DATA_DIR`. SQLite lives at `fitness_data.db` through `data_store.DATA_DB`; legacy JSON nutrition remains in `NUTRITION_FILE`.

| Store | Key fields | What persists | Retention/normalization |
| --- | --- | --- | --- |
| `food_logs` SQLite | `user_id`, unique `client_id`; indexes include `meal_id` | Canonical accepted, corrected, manual, and pending food rows with macros, source, original estimate, V2 item metadata, and vocab learned marker | Upsert by `client_id`; pending rows older than 7 days are removed during pending hydration; delete by client or meal also deletes refresh events. |
| `nutrition_data` SQLite / `NUTRITION_DATA` JSON | `user_id`, `date` or JSON entries | Legacy daily nutrition compatibility surface | Food-log totals take precedence when accepted rows exist. |
| `meal_review_snapshots` SQLite | `user_id`, `meal_id` | Full V2 review payload JSON, next item sequence, applied refreshes | Saved after initial intake and every refresh; deleted on accept/discard/stale cleanup. |
| `meal_acceptance_events` SQLite | `user_id`, `meal_id` | Acceptance/discard idempotency and feedback summary | Stores status `logged` or `discarded`, included client IDs, feedback fingerprint, skipped/deleted counts. |
| `personal_vocab` SQLite | `user_id`, `normalized_input` | Learned phrase to canonical nutrition estimate, accept/correct/skip/delete counters | Exact trusted after 3 accepts and 0 corrections; fuzzy trusted after 1 accept and 0 corrections. |
| `branded_lookup_cache` / `barcode_lookup_cache` SQLite | `user_id`, normalized text or barcode | Provider estimate JSON and source | TTL 180 days; provider details in PRD 06. |
| IndexedDB `fitMealIntakeQueueDB` | `queued_meals.client_id`, `meal_photos.photo_id` | Offline meal text, timestamps, auth scope, photo blobs, retry status | Removed immediately after server accept; discard removes metadata and photo blobs; orphaned photos are cleaned on boot. |
| Backup JSON | `data.food_logs`, `data.personal_vocab`, `data.meal_acceptance_events`, `data.meal_review_snapshots`, `data.nutrition` | Portable local backup | Export includes rows; import replays without wiping existing food logs. |

Food-log ordering differs by route: `data_store.get_food_logs` returns newest first by `logged_at` then ID, while `/api/food-logs/by-date/<date>` returns same-day entries sorted ascending by `logged_at` then ID for readable daily log order.

## 7. Enums & Constants

| Name | Values | Meaning |
| --- | --- | --- |
| Meal types | `breakfast`, `lunch`, `dinner`, `snack` | Allowed estimate/review meal buckets. |
| V2 refresh kinds | `add_item`, `edit_portion`, `followup_answer`, `choose_candidate`, `skip_item`, `delete_item`, `restore_item`, `set_meal_type` | Mutations the review card can request. |
| Request-ID required kinds | `add_item`, `edit_portion`, `choose_candidate`, `followup_answer` | Mutations that need idempotency IDs. |
| Item states | `included`, `skipped`, `deleted` | Included saves; skipped/deleted record negative feedback and can be restored before save. |
| V2 source kinds | `vision`, `text`, `branded`, `vocab`, `manual`; observed manual barcode kind `barcode_pending_source` | Source chip categories. `barcode_pending_source` is used by backend/UI but is not in the V2 UI allowlist, so the row badge checks it directly. |
| Policy statuses | `logged`, `pending_review` | Policy result. Current fresh intake is forced to pending review even if policy could log. |
| Correction states | `pending_review`, `accepted`, `corrected`, `manual` | Pending excluded from totals; accepted/corrected/manual count. `manual` is from add route defaults. |
| Confidence bands | `high`, `medium`, `low` | `high >= 0.75`, `medium >= 0.55 and < 0.75`, `low < 0.55` or invalid. |
| Policy reasons | `low_confidence`, `medium_confidence`, `ambiguous_input`, `implausible_calories`, `implausible_macros`, `implausible_sodium`, `missing_calories` | User-facing reason chips and save-block basis. |
| Estimate source tags | `ai_text_estimate`, `fallback_text_estimate`, `manual_review_estimate`, `barcode_pending_source`, `nutritionix`, `nutritionix_barcode`, `usda_fdc`, `usda_fdc_barcode`, `open_food_facts`, `open_food_facts_barcode`, `heb_product_page`, `local_cache`, `personal_vocab`, `stub_vision_estimate`, `vision_*`, composed sources such as `vision_*+nutritionix` | Estimate provenance. Provider internals in PRD 06; `stub_vision_estimate`/`vision_*` belong to PRD 05. |
| Text parser fallback reasons | `empty_input`, `needs_quantity`, `timeout`, `invalid_json`, `schema_mismatch`, `lock_timeout`, `all_endpoints_failed` | Why deterministic/manual fallback was used. |
| Ambiguous text tokens | `popcorn`, `movie`, `shared`, `leftover`, `leftovers`, `snacks`, `half`, `buffet`, `potluck`, `?`, `guessing`, `guess`, `some food`, `a bit`, `a few` | Tokens that lower confidence and route to review. |
| Barcode lengths | 8, 12, 13, 14 digits | Accepted normalized barcode lengths. |
| File MIME types | `image/jpeg`, `image/png`, `image/webp`, `image/gif` | Accepted photo MIME types for intake. |
| Size caps | 500 text chars; 4 photos; 6 MB per photo; 18 MB aggregate; 128 client ID chars; 64 timestamp chars | User and transport limits. |
| Nutrition plausible maxes | 5000 calories; 500 g protein/carbs/fat/fiber; 12000 mg sodium | Server review blocking and policy limits. |
| Pending review TTL | 7 days | Old pending rows and snapshots are removed during hydration. |
| Offline statuses | `pending`, `auth_required`, `conflicted`, `rejected`, `eviction_failed`, `inserted`, `already_synced`, `synced`, `discarded` | Sync queue state labels/behavior. Retryable statuses are `pending` and `auth_required`. |
| Nutrition targets | Calories default 2200; protein default `USER_SETTINGS.daily_protein_target_g`, else latest weight * 0.8, else 148 g; carbs/fat split remaining calories 55%/45% after protein | Macro-card target defaults. |
| Nutrition context thresholds | Sodium next-day context 2300 mg; late meal hour 20; under-fueled calories 60%; under-fueled protein 50% | Warning and next-day coaching context. |
| Undo window | 30000 ms | Toast undo for auto-logged legacy path; current fresh intake normally pending. |

## 8. Integration Points

- Nutrition data sources feed text lookup, barcode lookup, candidate choices, and verified source metadata; see [06 Nutrition Data Sources](06-nutrition-data-sources.md).
- Photo capture and local VLM estimation enter through `POST /api/meal-intake` and the shared estimate schema, but photo-specific capture, image privacy, and vision prompt/model behavior belong to PRD 05.
- Workout adaptation listens after accepted nutrition rows and receives only accepted/corrected/manual rows, not pending review rows.
- Dashboard macro card and nutrition history consume accepted food logs and pending counts.
- Backup export/import preserves food logs, review snapshots, acceptance events, and personal vocabulary.
- `tests/test_meal_intake_review_contract.py` and `tests/test_meal_logging_e2e.py` are the primary server-side coverage for V2 snapshot/refresh/accept lifecycle and text/photo pending accept/discard flows.
- Auth and CSRF are shared with the server-rendered app shell and owner session model documented in `01-auth-and-account.md`.

## 9. Permissions & Security

All routes are owner-session routes in the Flask app. The frontend sends same-origin credentials and the configured CSRF header for mutating requests. The offline queue records auth scope before replay and refuses to post queued meals under a mismatched scope.

The intake path strips unknown model fields, prompt echoes, model traces, raw image references, provider payloads, and image bytes from public estimate responses. Raw food photos are not included in normal server responses or backups. Offline photo blobs are a temporary browser-only carveout and are deleted after server acceptance or explicit queue discard.

Barcode camera scanning happens locally in the browser; the decoded barcode string is the only camera-derived value sent to the server. V2 review item estimates strip raw provider URLs and other prohibited estimate keys; source links arrive only through `items[].source.link` and are opened in an in-app sandboxed iframe after client-side same-origin sanitization. External provenance links in the legacy single-item card are rendered as ordinary safe external links.

## 10. Business Rules

- Pending review is the product default for fresh meal intake in the current code. The policy engine can return `logged`, but `POST /api/meal-intake` forces fresh captures into `pending_review`.
- Pending rows never count toward calories, macros, warnings, or workout adaptation. They only count as pending-review context.
- Accepted food logs for a date override legacy daily nutrition JSON. Legacy JSON is used only when no accepted food logs exist for that day.
- Local date precedence is `local_date`, then `local_iso`, then older `local_timestamp`, then server today; `logged_at` precedence is `local_iso`, then `local_timestamp`, then server now.
- Save-blocked V2 items must be resolved before Save. Dismissing the follow-up question alone does not unblock a blocked item.
- Retry of a pending entry uses a new client ID for the new estimate, then best-effort deletes the old pending row. If cleanup fails, the UI warns that the old row may reappear.
- Offline replay uses the original client ID and timestamps, so a meal saved before midnight does not move to the next day on sync.
- Personal vocabulary exact trust requires three accepts and zero corrections. Fuzzy trust requires at least one accept and zero corrections. Skips/deletes record negative feedback but do not create a trusted mapping.
- Sodium at or above 2300 mg creates next-day context; meals at or after 20:00 can create late-meal next-day context.

## 11. Config & Environment

| Env/config | Default | Behavior when unset |
| --- | --- | --- |
| `DATA_DIR` | App default data directory | Local JSON/SQLite files are stored under the resolved data directory. |
| `SECRET_KEY` | Required in production/test setup | Used by Flask auth/session/CSRF. |
| `LM_STUDIO_MEAL_TEXT_TIMEOUT_SEC` | 45 seconds | Text parser local model timeout. |
| `NUTRITIONIX_APP_ID`, `NUTRITIONIX_APP_KEY` | Unset | Nutritionix text/barcode provider is skipped; details in PRD 06. |
| `USDA_FDC_API_KEY` | Unset | USDA provider is skipped; details in PRD 06. |
| Vision model envs | See `docs/MEAL_MODEL_DECISION.md` | Photo route behavior belongs to PRD 05. |

## 12. Test Coverage

- `tests/test_food_log_api.py` covers `/api/add-nutrition` sanitization, `client_id` retry/replace behavior, generated dual-write IDs, and rollback around legacy JSON/SQLite failures.
- `tests/test_fit134_review_ui.py` statically guards the V2 review UI: collapsed totals, add item, source viewer, portion edits, follow-up budget, blocked save, skip/delete/restore, meal type persistence, pending hydration, request IDs, and live accept body shape.
- `tests/test_fit142_barcode_ui.py` guards barcode UI controls, scanner cancellation, decoded-barcode-only payloads, normalization, residual 404 handling, idempotent barcode draft IDs, unknown-barcode manual review, offline behavior, and styles.
- `tests/test_fit145_offline_queue.py` guards IndexedDB schema, blob storage outside localStorage, offline timestamps/client IDs, replay contract, auth-scope handling, retry/discard UI, photo cleanup, online hooks, and privacy copy.
- `tests/test_fit150_pending_refresh.py` guards disabled controls and visual state while V2 refresh is in flight.
- `tests/test_fit210_manual_review_badge.py` guards the persistent manual-review badge for unresolved barcode cards and its accessible label.
- `tests/test_fit219_review_card_a11y.py` guards focusable review cards, focus preservation across rerenders, ARIA controls, distinct photo-remove labels, and confidence-band text.
- `tests/test_backup_import_food_logs.py` covers idempotent food-log backup import and personal vocabulary / meal acceptance event round-trip.

Coverage gaps: server-side V2 refresh and accept endpoint coverage exists in `tests/test_meal_intake_review_contract.py` and `tests/test_meal_logging_e2e.py`; barcode provider behavior is mostly covered in nutrition source tests; personal-vocabulary trust behavior has focused tests elsewhere but is not exhaustively represented in the assigned test list. [TBC] Full test inventory outside assigned sources may cover more.

## 13. Gaps & Issue Candidates

### IC-1: Harden meal accept against client-mutated corrections
- **Type:** Bug
- **Priority:** high
- **Where:** `app.py` `POST /api/meal-intake/<client_id>/accept`
- **Problem:** The accept path accepts client-submitted estimates and correction metadata, then persists accepted/corrected rows. The server does re-sanitize and block unresolved V2 items, but the product trust boundary should be explicit: the browser should not be able to assert correction state or overwrite an already accepted row outside the intended review snapshot contract.
- **Why it matters:** Nutrition rows drive coaching and learned vocabulary, so client-side tampering can poison future recommendations.
- **Acceptance criteria:**
  - Server derives corrected/accepted state from the stored review snapshot and field diffs, not from arbitrary client flags.
  - Replays cannot overwrite an already terminal accepted row with a different estimate.
  - Blocked V2 items cannot be bypassed by legacy single-estimate accept payloads.
  - Endpoint tests cover snapshot accept, stale accept, and tampered correction payloads.
- **Duplicate-of:** FIT-231

### IC-2: Add confirmation and undo to food-log deletion
- **Type:** Improvement
- **Priority:** high
- **Where:** `templates/index.html` meal detail controls; `static/js/app.js` food-log detail delete flow; `DELETE /api/meal-intake/<client_id>`
- **Problem:** Food-log detail deletion is destructive and adjacent to edit controls. The code has undo for the legacy auto-log toast path and restore for V2 skipped/deleted review items, but canonical food-log deletion still needs an explicit confirmation and recovery affordance.
- **Why it matters:** A mistaken tap can remove accepted nutrition that affects the day's fuel totals and workout context.
- **Acceptance criteria:**
  - Detail delete requires confirmation that names the entry being removed.
  - Deletion offers an undo path or delayed finalization.
  - Macro card and food log refresh after delete and undo.
  - Tests cover accidental double-click/keyboard activation and undo.
- **Duplicate-of:** FIT-228

### IC-3: Return safe photo provenance in canonical food-log reads
- **Type:** Data-contract
- **Priority:** medium
- **Where:** `app.py` `GET /api/food-logs/by-date/<date>`; `data_store.py` `original_estimate_json`
- **Problem:** The date-based food-log read returns bounded top-level fields including `from_image`, but it omits safe original estimate provenance such as vision source, retention policy, and verified downstream source. The data is available in `original_estimate_json`, but not exposed to the detail surface.
- **Why it matters:** The owner cannot tell later whether a canonical row came from text, barcode, or photo estimation without losing the raw-photo privacy guarantee.
- **Acceptance criteria:**
  - Food-log reads include a safe provenance object derived from the stored estimate.
  - Raw images, prompts, raw traces, and provider payloads remain excluded.
  - Meal detail shows photo/vision provenance and provider provenance distinctly.
  - Backup/export keeps the same safe provenance contract.
- **Duplicate-of:** FIT-229

### IC-4: Reconcile forced pending review with the auto-log contract
- **Type:** Docs
- **Priority:** medium
- **Where:** `docs/MEAL_INTAKE_CONTRACT.md`; `app.py` `POST /api/meal-intake`; `meal_log_policy.py`
- **Problem:** The public contract still describes `logged` as a normal immediate success state when confidence is high. The current route forces all fresh submissions into `pending_review`, making the policy engine advisory for this endpoint.
- **Why it matters:** Future agents may rebuild auto-log behavior from the stale contract and accidentally start counting uncertain meals without review.
- **Acceptance criteria:**
  - Contract states whether auto-log is currently disabled, feature-flagged, or planned.
  - Tests assert the intended status for high-confidence text/barcode estimates.
  - UI copy matches the server's review-first behavior.
  - Policy module docs distinguish theoretical policy from route behavior.
- **Duplicate-of:** none

### IC-5: Preserve personal vocabulary trust boundaries
- **Type:** Data-contract
- **Priority:** high
- **Where:** `personal_vocab.py`; `meal_text_parser.py`; V2 accept feedback path
- **Problem:** Personal vocabulary can turn repeated owner phrases into high-confidence estimates. The code has accept/correct/negative counters, but this path is sensitive to fallback estimates, corrected estimates, and skipped/deleted feedback; trust rules must stay aligned with source authority.
- **Why it matters:** A learned bad mapping silently changes future meal estimates before the owner sees provider evidence.
- **Acceptance criteria:**
  - Fallback or low-authority estimates cannot become trusted vocabulary without explicit accepted evidence.
  - Corrections immediately remove exact/fuzzy trust until enough new accepted evidence exists.
  - Skipped/deleted items record negative feedback and do not create trusted mappings.
  - Tests cover exact trust, fuzzy trust, correction downgrade, and fallback-source rejection.
- **Duplicate-of:** FIT-259

### IC-6: Add visible retry scheduling for offline meal sync
- **Type:** Improvement
- **Priority:** medium
- **Where:** `static/js/app.js` offline meal queue; sync queue modal
- **Problem:** Offline meal sync retries serially on boot/reconnect and manual retry, but the queue does not expose a next retry time, backoff policy, or maximum retry behavior. Failed entries can remain as `pending` or `auth_required` without a clear schedule.
- **Why it matters:** The owner needs to know whether a saved meal is actively retrying, waiting for auth, or stuck.
- **Acceptance criteria:**
  - Queue rows show last attempt time, next automatic retry condition, and attempt count.
  - Network/server failures use a documented backoff or explicit reconnect-only policy.
  - Auth-required entries explain the account mismatch and do not retry until scope is refreshed.
  - Tests cover pending, auth-required, rejected, conflicted, and eviction-failed display states.
- **Duplicate-of:** none
