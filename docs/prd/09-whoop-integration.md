# WHOOP Integration — PRD

> **Sources:** README.md; docs/VISION.md; docs/PRD.md; docs/CURRENT_STATE.md; app.py; whoop_client.py; whoop_store.py; whoop_recommendations.py; scripts/whoop_sync.py; templates/index.html; static/js/app.js; static/css/whoop.css; tests/test_whoop_client.py; tests/test_whoop_oauth.py; tests/test_whoop_store.py; tests/test_whoop_freshness.py; tests/test_whoop_import_sync_backup.py; tests/test_whoop_recommendations.py; tests/test_whoop_source_conflicts.py; tests/test_whoop_sync.py; tests/test_whoop_ui_contract.py
> **Routes:** /api/whoop/status; /api/whoop/connect/start; /api/whoop/callback; /api/whoop/disconnect; /api/whoop/delete-data; /api/whoop/sync; /api/whoop/import-csv; /api/whoop/imports; /api/whoop/recommendation-signals
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

## 1. Overview

The WHOOP integration brings recovery, strain, sleep, workout energy, and related biometric context into the local Fitness Dashboard without making WHOOP the primary workout planner. Its job is to provide a bounded wearable modifier: fresh scored WHOOP facts can make a recommendation more conservative, add fueling and sleep context, or flag a source conflict, but they must not replace the deterministic training engine or Apple Health load source.

The owner can connect WHOOP through OAuth when API credentials are configured, manually sync recent data, disconnect authorization, delete local WHOOP data, or import a CSV when OAuth is unavailable. OAuth material is stored outside normal app data through a protected material file, while normalized daily facts are stored in local SQLite under `DATA_DIR`. Backups export normalized WHOOP facts only; token material and raw upstream payloads are explicitly excluded.

The user-facing promise is honest degradation. Missing configuration shows "config missing" rather than a dead connect path. Disconnected historical imports are marked CSV-only. Stale, calibrating, pending, or unscored facts remain visible for context but do not reduce workout load. Terminal OAuth failures mark the connection as reauthorization-required. A source conflict between Oura readiness and WHOOP recovery is shown and resolved conservatively.

Automatic ingestion is partially implemented. `scripts/whoop_sync.py` defines a bounded sync runner suitable for launchd or cron, but it explicitly does not install scheduling. The app therefore supports manual sync and a callable backend sync contract; actual owner-machine scheduling is [TBC] outside this repo.

## 2. User-Facing Surfaces

### Settings row

The Settings view contains a "WHOOP fallback" integration row. The row shows a compact status chip, a Connect button, a Sync button, a Disconnect button, and a Delete data button. Detail rows show Connection, Data through, Recovery, Source, Attention, and Conflict when relevant.

The row is intended as a fallback/source-of-context surface, not the primary wearable hub. Open Wearables is the generic add-wearable surface; WHOOP remains the durable WHOOP source of truth for direct WHOOP data.

### WHOOP connect/import modal

The WHOOP modal has two intake paths:

- Live sync: opens WHOOP OAuth via `/api/whoop/connect/start`. A fallback link is displayed when the popup cannot be opened.
- Manual import: accepts pasted CSV text or an uploaded `.csv`/`text/csv` file and sends it to `/api/whoop/import-csv`.

The modal disables duplicate submissions while connect, import, sync, disconnect, or delete actions are in flight. OAuth completion is detected by polling popup close/focus state every 1.5 seconds and expires the pending UI after 5 minutes.

### Dashboard and recommendation surfaces

WHOOP facts feed the recommendation stack rather than appearing as a standalone planner. `/api/dashboard`, `/api/next-workout`, and `/api/recommendation/smart` can apply WHOOP modifiers and expose recommendation source details. `/api/freshness` includes WHOOP freshness so the UI can reconcile data status without triggering recommendation mutation.

### Backup and restore surfaces

Backup export includes normalized WHOOP daily facts only. Backup import validates those facts before mutating local data. The backup path is not a token backup, raw payload export, or upstream WHOOP archive.

## 3. Field Inventory

### OAuth configuration

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `WHOOP_CLIENT_ID_FILE` | Environment path | No | `.whoop-client-id` in the repository root | File must be readable and contain non-empty text | Points to the WHOOP OAuth client ID without storing it inline in code. |
| Keychain item `fitness-dashboard-whoop-client-secret` | macOS Keychain secret | Yes for OAuth | None | Read with `security find-generic-password`; 5 second timeout | Holds the OAuth client secret outside repo and app JSON data. |
| `WHOOP_SCOPES` | Environment string | No | `offline read:recovery read:cycles read:sleep read:workout` | Whitespace-separated scope names | Overrides requested WHOOP OAuth scopes. |
| `WHOOP_PROTECTED_MATERIAL_DIR` | Environment path | No | `~/Library/Application Support/Fitness Dashboard/secrets` | Directory is created if needed; material file is chmod 0600 | Stores access/refresh token material outside normal dashboard data. |
| Redirect URI | URL | Yes | `public_base_url()/api/whoop/callback` | Generated by app | The callback registered with WHOOP and used to exchange OAuth codes. |

### Connection status payload

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `status` | Enum | Yes | `missing_config` or `disconnected` | See Section 7 | Current connection state shown in Settings. |
| `configured` | Boolean | Yes | Derived | True only when client ID and client secret are available | Whether live OAuth can be offered. |
| `connected` | Boolean | Yes | Derived | True when status is connected and material is available | Whether API sync can run. |
| `reauth_required` | Boolean | Yes | `false` | Set on terminal auth failures | Whether the owner must reconnect WHOOP. |
| `connected_at` | ISO datetime/null | No | null | Stored in SQLite | When OAuth connection was established. |
| `last_successful_sync_at` | ISO datetime/null | No | null | Stored in SQLite | Last completed sync/import success. |
| `last_sync_attempt_at` | ISO datetime/null | No | null | Stored in SQLite | Last attempted sync/import, successful or failed. |
| `last_error` | String/null | No | null | Redacted before storage for sync/OAuth failures | Human-safe error shown as attention text. |
| `scopes` | String array | No | Configured scopes | JSON list in SQLite | OAuth scopes attached to the connection. |
| `freshness` | Object | Yes | Derived | See freshness fields below | Status of latest normalized WHOOP fact. |
| Flattened fact fields | Date/number/string fields | No | null | Today's fact when present, else latest fact | `local_date`, `recovery_score`, `recovery_band`, `strain`, `sleep_performance_pct`, `sleep_need_gap_min`, and `score_state` are exposed at top level; nested `today`/`latest` fact objects are not returned. |

### Freshness fields

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `status` | Enum | Yes | `missing` | `fresh`, `aging`, `stale`, `missing` | Whether data is current enough to modify recommendations. |
| `last_data_point` | Date/null | No | null | Local date string | Date of latest normalized WHOOP daily fact. |
| `last_sync_attempt` | ISO datetime/null | No | null | Stored in connection or sync run | Last attempted sync or import. |
| `score_state` | Enum/null | No | null | See score states | WHOOP scoring state for latest fact. |
| `connected` | Boolean | Yes | Derived | From connection | Whether the account is live-connected. |
| `connection_status` | Enum | Yes | `disconnected` | From connection row | Connection status independent from data freshness. |
| `csv_only` | Boolean | No | false | Derived | True when latest useful data came from CSV and no live connection exists. |
| `source_kind` | Enum/null | No | null | Derived | `csv_only` when latest useful data came from CSV import with no live connection; otherwise absent/null. |
| `reauth_required` | Boolean | No | false | Freshness node | Drives reauth chip state in `/api/freshness` and the UI. |

### Manual CSV import

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `csv` | UTF-8 text | Yes unless file uploaded | None | Non-empty; UTF-8; max 512 KiB | Pasted CSV payload. |
| Uploaded file | File | Yes unless `csv` text sent | None | Raw bytes max 512 KiB; UTF-8 decode required | CSV file import path. |
| `record_type` / `type` | Enum | No | `recovery` | `recovery`, `sleep`, `cycle`, `workout`; unsupported rows are skipped | Selects normalization rules. |
| `date` / `local_date` / `day` | Date | Required after normalization | None | `YYYY-MM-DD`; cannot be later than tomorrow | Local daily bucket. |
| `start_time` / `end_time` | ISO datetime | No | None | Parsed when present | Used to infer local day; sleep prefers wake/end date. |
| `timezone_offset` | String | No | None | `+HH:MM` or `-HH:MM` style offset if parseable | Converts timestamp to local bucket. |
| `nap` | Boolean-ish | No | false | `1`, `true`, `yes`, `y` accepted | Sleep naps are ignored for daily facts. |
| `score_state` / `state` | Enum | No | `SCORED` | Local enum | Controls whether metrics can affect recommendations. |

### Normalized WHOOP daily fact

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `local_date` | Date | Yes | None | `YYYY-MM-DD` | Daily bucket used by dashboard. |
| `provider` | String | Yes | `whoop` | Constant | Fact source. |
| `recovery_score` | Number/null | No | null | 0-100 | WHOOP recovery percentage. |
| `recovery_band` | Enum/null | No | Derived when score present | `low`, `medium`, `high` | Human-readable recovery tier. |
| `strain` | Number/null | No | null | 0-21 | WHOOP strain. |
| `sleep_performance_pct` | Number/null | No | null | 0-100 | Sleep performance percentage. |
| `sleep_need_gap_min` | Number/null | No | null | 0-1440 | Estimated minutes of unmet sleep need. |
| `workout_kj` | Number/null | No | null | 0-20000 | Workout energy from WHOOP workouts. |
| `hrv_rmssd` | Number/null | No | null | 0-300 | Heart-rate variability. |
| `resting_hr` | Number/null | No | null | 25-220 | Resting heart rate. |
| `respiratory_rate` | Number/null | No | null | 5-40 | Respiratory rate. |
| `spo2` | Number/null | No | null | 50-100 | Blood oxygen percentage. |
| `skin_temp` | Number/null | No | null | -20 to 60 | Skin temperature value from WHOOP payload/import. |
| `percent_recorded` | Number/null | No | null | 0-100 | Data capture percentage when supplied. |
| `score_state` | Enum | Yes | `SCORED` | See score states | Determines whether metrics can be promoted. |
| `last_sync_attempt` | ISO datetime/null | No | null | Stored from sync run | Last operation that produced or attempted this fact. |
| `projected_at` | ISO datetime | Yes | Now | Internal timestamp | When raw records were projected into daily facts. |

### Recommendation signal payload

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `source` | String | Yes | `whoop` | Constant | Signal source. |
| `freshness_status` | Enum | Yes | From freshness | `fresh`, `aging`, `stale`, `missing` | Whether the fact can affect recommendations. |
| `score_state` | Enum/null | No | From fact | Local score enum | Pending/calibrating/unscored data is display-only. |
| `display_only` | Boolean | Yes | Derived | True for stale, missing, unscored, or metricless facts | Prevents recommendation modification. |
| `bounded_confidence` | Enum | Yes | `none`, `low`, `medium`, `high` | Derived | User-facing confidence in the WHOOP modifier. |
| `applied_modifiers` | String array | No | `[]` | May contain multiple of `deload`, `caution`, `sleep_priority`, `fuel_up` | Recommendation effects requested by WHOOP signals; empty when display-only. |
| `explanations` | String array | Yes | [] | Generated | Plain-language reason text. |
| `source_conflict` | Object/null | No | null | Derived from Oura vs WHOOP bands | Conflict marker and conservative source. |

## 4. Interactions & Flows

### Load WHOOP status

Trigger -> Settings render, freshness refresh, or OAuth completion refresh.

Behavior -> The frontend calls `/api/whoop/status`, merges the connection state with `/api/freshness`, then renders the chip and detail rows. Freshness can override generic connected/disconnected status when the latest data is stale, missing, pending, unscored, calibrating, CSV-only, or in conflict.

Validation -> No request body. Response must not include access tokens, refresh tokens, client secrets, material references, or raw WHOOP payloads.

API -> `GET /api/whoop/status`.

Success -> UI shows an accurate state and enables only meaningful actions. Missing config disables the live OAuth path but keeps CSV import available.

Failure -> UI records a WHOOP error state and shows attention text without exposing secrets.

### Start OAuth connection

Trigger -> Owner clicks Connect from the WHOOP modal live-sync section.

Behavior -> The frontend opens a blank popup, posts to `/api/whoop/connect/start`, receives an authorization URL, and navigates the popup to WHOOP. The backend creates a single-use OAuth state tied to the current data user binding and a 10 minute expiry.

Validation -> Requires CSRF mutation guard. Backend requires client ID and Keychain client secret. OAuth state is generated with `secrets.token_urlsafe(32)` and stored before returning the URL.

API -> `POST /api/whoop/connect/start`.

Success -> Response includes `authorization_url` and public connection state. UI marks OAuth pending and refreshes status after popup close/focus.

Failure -> Missing config returns `missing_whoop_config` without a usable authorization URL. UI keeps the CSV import path available.

### Complete OAuth callback

Trigger -> WHOOP redirects the browser to `/api/whoop/callback?code=...&state=...`.

Behavior -> Backend consumes the OAuth state under the WHOOP lock. Valid states are single-use, unexpired, and bound to the same data user. The app exchanges the code for tokens, validates that access and refresh tokens exist, writes protected material, and stores a connected row.

Validation -> Missing `state` returns `missing_state` and missing `code` returns `missing_code` (both 400). Invalid, expired, consumed, or user-mismatched states return `invalid_state` (400) after the Open Wearables fallback is attempted. Token payload must include both `access_token` and `refresh_token`.

API -> `GET /api/whoop/callback`.

Success -> Browser navigations redirect to `/#settings`; JSON clients receive status. Cache-control is `no-store`.

Failure -> OAuth exchange failures return `whoop_oauth_failed` with redacted detail. If the state is not a direct WHOOP OAuth state, the callback attempts Open Wearables WHOOP completion as a fallback; see [10-open-wearables-integration.md](10-open-wearables-integration.md).

### Manual sync

Trigger -> Owner clicks Sync, scheduled runner calls the backend, or another server flow invokes `_run_whoop_sync`.

Behavior -> Backend enforces an in-process lock and cross-process file lock. It loads protected token material, creates a sync run, fetches recovery, sleep, cycle, and workout collections from WHOOP for the requested window, normalizes records, upserts raw normalized records, projects daily facts, and updates connection timestamps.

Validation -> Connection must be `connected`; protected session token must exist; config must be present. API route accepts optional `days_back` from 1 to 30 and defaults to 7. The scheduled script uses stricter mode contracts: normal 1-14 days default 2, repair 1-14 default 7, backfill 30-90 default 30.

API -> `POST /api/whoop/sync`.

Success -> Returns sync run metadata, records upserted, and public connection status.

Failure -> Concurrent sync returns `whoop_sync_in_progress`. Missing connection/token/config returns 409 or 503 with stable codes. Terminal WHOOP auth errors, including HTTP 400 or 401 from WHOOP, mark `reauth_required`; retryable transport/API errors finish the sync run with retryable metadata and redacted error text.

### Scheduled sync runner

Trigger -> Owner-managed launchd/cron or manual CLI invocation of `scripts/whoop_sync.py`.

Behavior -> The script resolves a sync mode and day window, loads a backend adapter from `WHOOP_SYNC_BACKEND` or defaults to `app:_run_whoop_sync`, retries retryable failures up to 3 attempts, and prints a redacted summary.

Validation -> Mode must be `normal`, `repair`, or `backfill`; days must fit the mode contract. Backoff is exponential and capped at 30 seconds. The script does not create launchd or cron automation.

API -> Internal backend call, not an HTTP endpoint.

Success -> Exit code 0 and summary with status, mode, days, attempts, sync run id, records upserted, and message.

Failure -> Exit code 1 with redacted non-secret summary. Actual installed schedule is [TBC] because no launchd plist or cron setup is part of this repo.

### CSV import

Trigger -> Owner pastes CSV text or uploads a CSV file in the WHOOP modal.

Behavior -> Backend enforces byte, encoding, and row limits; parses CSV with headers; normalizes supported row types; rejects impossible metric values; skips unsupported record types; writes a `csv_import` sync run; projects daily facts.

Validation -> Max payload 512 KiB; max rows 5000; UTF-8 required; record type must be one of the supported types to import; local date cannot be later than tomorrow; metric bounds are listed in Section 7.

API sync payloads can mark `score.user_calibrating === true` to force `CALIBRATING`. Flat CSV rows can use a truthy `user_calibrating` value (`1` or case-insensitive `true`), or callers can set `score_state=CALIBRATING` directly.

API -> `POST /api/whoop/import-csv`.

Success -> Response includes `records_upserted`, public connection state, and sync metadata. If no live connection exists, the UI marks useful data as CSV-only.

Failure -> Oversized bytes return `whoop_csv_too_large`; row flood returns `whoop_csv_too_many_rows`; invalid UTF-8 returns `invalid_whoop_csv_encoding`; impossible/non-numeric metrics return `invalid_whoop_csv_metric`; no importable rows returns `empty_whoop_csv`.

### Disconnect

Trigger -> Owner clicks Disconnect.

Behavior -> Backend loads protected material if present, attempts to revoke WHOOP access, invalidates pending OAuth states, deletes local protected token material, and marks connection disconnected.

Validation -> Requires CSRF mutation guard and WHOOP lock. Revoke failures are redacted; local token deletion failures are terminal.

API -> `POST /api/whoop/disconnect`.

Success -> Returns disconnected public connection and revocation status (`revoked`, skipped, or failed with redacted detail).

Failure -> If protected material cannot be deleted, returns `whoop_disconnect_failed` and preserves accurate error state.

### Delete local WHOOP data

Trigger -> Owner confirms Delete data.

Behavior -> Backend invalidates pending states, clears local WHOOP records, daily facts, sync runs, connection state, and protected material.

Validation -> Requires CSRF mutation guard and WHOOP lock.

API -> `POST /api/whoop/delete-data`.

Success -> Returns cleared connection state. UI clears WHOOP status and invalidates dashboard/recommendation caches.

Failure -> Concurrent sync/delete lock returns a lock error; protected material deletion failure returns a server error.

### Apply recommendation modifier

Trigger -> Dashboard, next workout, or smart recommendation generation.

Behavior -> The recommendation engine builds WHOOP signals from the selected daily fact and freshness. Fresh scored data may apply a deload/caution/sleep/fueling modifier. Stale, CSV-only disconnected historical data, pending, unscored, calibrating, and metricless facts are display-only. Caution/deload RPE reductions have a floor of 5.

Validation -> Modifier is idempotent through a signature tag and never increases load. Source conflicts use the conservative lower readiness band.

API -> Consumed by `/api/dashboard`, `/api/next-workout`, `/api/recommendation/smart`, and `/api/whoop/recommendation-signals`.

Success -> Recommendation includes source explanations and adjusted workout/nutrition values when appropriate.

Failure -> WHOOP missing or display-only does not lower confidence or block deterministic recommendations.

## 5. API Endpoints

| Method | Path | Auth | Trigger | Key params | Response shape | Real/Mock |
|---|---|---|---|---|---|---|
| GET | `/api/whoop/status` | Owner session | Settings/freshness load | None | Public connection, freshness, and flattened latest/today fact fields | Real local state |
| POST | `/api/whoop/connect/start` | Owner session + CSRF | OAuth connect | None | Authorization URL, connection | Real WHOOP OAuth |
| GET | `/api/whoop/callback` | Owner session + single-use OAuth state | WHOOP redirect | `code`, `state` | Redirect or JSON connection | Real WHOOP OAuth; also Open Wearables fallback; browser session cookie must be present |
| POST | `/api/whoop/disconnect` | Owner session + CSRF | Disconnect | None | Connection, revocation metadata | Real local disconnect; best-effort WHOOP revoke |
| POST | `/api/whoop/delete-data` | Owner session + CSRF | Delete local facts | None | Cleared connection | Real local deletion |
| POST | `/api/whoop/sync` | Owner session + CSRF | Manual sync | JSON `days_back` optional | Sync run, connection | Real WHOOP API when configured |
| POST | `/api/whoop/import-csv` | Owner session + CSRF | CSV import | JSON `csv` or multipart file | Sync run, records upserted, connection | Real local import |
| GET | `/api/whoop/imports` | Owner session | Import history | None | Last 20 `csv_import` runs | Real local state |
| GET | `/api/whoop/recommendation-signals` | Owner session | Diagnostics/recommendations | None | Signals, connection, source conflict | Real local computation |

Endpoint detail:

- OAuth responses are marked `Cache-Control: no-store`, `Pragma: no-cache`, and `Referrer-Policy: no-referrer`.
- Sync calls serialize through both a Python lock and a file lock at `DATA_DIR/whoop_sync.lock`.
- WHOOP API collection fetches paginate on `next_token`, refresh once on 401, and retry retryable errors up to 2 times with 1 s then 2 s backoff at the client layer (delay capped at 4 s).
- API error redaction removes token/code/state/client secret/Bearer material before storing or returning errors.
- `/api/whoop/callback` must not log raw callback query strings in access logs according to UI contract tests.

## 6. Data Model & Persistence

### SQLite store

WHOOP state lives in `DATA_DIR/whoop.sqlite3`.

`whoop_connection` stores the single connection row:

- `id` fixed to 1.
- `status`, `connected_at`, `disconnected_at`, `last_successful_sync_at`, `last_sync_attempt_at`, `last_error`, `scopes_json`, `material_ref`, `access_token_expires_at`, `reauth_required`, `updated_at`.
- Legacy `access_token` and `refresh_token` columns may exist from migrations but are nulled by current migration behavior.

`whoop_oauth_states` stores single-use OAuth states:

- `state`, `redirect_uri`, `user_binding`, `created_at`, `expires_at`, `consumed_at`.
- States expire after 10 minutes by default and are consumed under `BEGIN IMMEDIATE`.

`whoop_sync_runs` stores sync/import history:

- `run_id`, `reason`, `window_start`, `window_end`, `status`, `started_at`, `completed_at`, `records_upserted`, `retryable`, `redacted_error`.
- Reasons include API modes and `csv_import`; backup import uses a backup-specific run reason in restore logic.

`whoop_records` stores normalized per-upstream records:

- Primary key is `(record_type, upstream_id)`.
- Fields include local date, start/end timestamps, score state, every metric field listed in Section 3, upstream updated timestamp, import timestamp, and sync run id.

`whoop_daily_facts` stores the recommendation-ready projection:

- Primary key is `local_date`.
- Projection merges record types for each local date.
- Only `SCORED` rows promote metric values into daily facts. Pending/calibrating/unscored rows can preserve score state but do not promote recovery metrics.

### Protected material store

OAuth access and refresh values are stored outside SQLite in a JSON material file:

- Material ref constant: `fitness-dashboard-whoop-oauth-material`.
- Default directory: `~/Library/Application Support/Fitness Dashboard/secrets`.
- Override: `WHOOP_PROTECTED_MATERIAL_DIR`.
- Filename includes a SHA-256 namespace of the absolute DB path.
- File fields are `session_value`, `renewal_value`, and `stored_at`.
- File permission is restricted to 0600 and writes use atomic replace.
- If database save fails after material write, material is deleted.

### Backup behavior

Backup export includes only normalized `whoop_daily_facts`. Backup import rejects:

- Token or material fields: `access_token`, `refresh_token`, `token_ref`, `material_ref`.
- Raw payload fields: `raw`, `raw_json`, `payload`, `provider_payload`.
- Client secret fields.
- Malformed shapes, non-object entries, invalid dates, and impossible metric values.

Restore validates WHOOP facts before mutating local JSON/data state.

## 7. Enums & Constants

### OAuth scopes

| Value | Meaning |
|---|---|
| `offline` | Request refresh-token access. |
| `read:recovery` | Read recovery data. |
| `read:cycles` | Read cycle/strain data. |
| `read:sleep` | Read sleep data. |
| `read:workout` | Read workout data. |

Open Wearables managed WHOOP seeding also uses `read:body_measurement`; direct WHOOP default scopes do not.

### Record types

| Value | Meaning |
|---|---|
| `recovery` | Recovery score, HRV, resting HR, respiratory rate, SpO2, skin temperature. |
| `sleep` | Sleep performance and sleep need gap; naps are ignored. |
| `cycle` | WHOOP strain. |
| `workout` | Workout energy in kilojoules. |

### Score states

| Value | Meaning |
|---|---|
| `SCORED` | Metrics can be promoted and may modify recommendations if fresh. |
| `PENDING_SCORE` | Data exists but WHOOP has not scored it; display-only. |
| `UNSCORABLE` | WHOOP cannot score the record; display-only. |
| `CALIBRATING` | User/device calibration state; display-only. |

Other upstream score strings are [TBC]; local behavior treats known non-scored states as display-only.

### Freshness states

| Value | Meaning |
|---|---|
| `fresh` | Latest data point is under 24 hours old. |
| `aging` | Latest data point is at least 24 hours old and under 48 hours old. |
| `stale` | Latest data point is 48 hours old or older. |
| `missing` | No usable local date exists. |

### Connection/UI states

| Value | Meaning |
|---|---|
| `missing_config` | Client ID/secret missing; live OAuth unavailable. |
| `disconnected` | No live WHOOP connection. |
| `connected` | OAuth connection exists. |
| `syncing` | Manual sync is in progress. |
| `fresh` | Fresh WHOOP data available. |
| `aging` | Data available but aging. |
| `stale` | Data available but stale. |
| `missing` | No local WHOOP facts. |
| `pending_score` | WHOOP score pending. |
| `unscorable` | WHOOP cannot score data. |
| `calibrating` | WHOOP user/device calibration. |
| `reauth_required` | OAuth refresh/auth failed terminally. |
| `csv_only` | Useful data exists only through manual import and no live connection exists. |
| `source_conflict` | Oura and WHOOP readiness bands conflict materially. |
| `error` | Last operation failed. |

### Recovery/readiness bands

| Band | WHOOP threshold | Oura threshold |
|---|---:|---:|
| `low` | `< 45` | `< 65` |
| `medium` | `45-66.999...` | `65-79.999...` |
| `high` | `>= 67` | `>= 80` |

### Recommendation thresholds

| Signal | Threshold | Action |
|---|---:|---|
| Recovery score | `< 45` | `deload` |
| Recovery score | `< 60` | `caution` |
| Strain | `>= 18` | `deload` |
| Strain | `>= 15` | `caution` |
| Sleep performance | `< 70%` | `deload` |
| Sleep performance | `< 85%` | `caution` |
| Sleep need gap | `>= 60 min` | `sleep_priority` and `fuel_up` |

### Metric bounds

| Field | Minimum | Maximum |
|---|---:|---:|
| `recovery_score` | 0 | 100 |
| `strain` | 0 | 21 |
| `sleep_performance_pct` | 0 | 100 |
| `sleep_need_gap_min` | 0 | 1440 |
| `workout_kj` | 0 | 20000 |
| `hrv_rmssd` | 0 | 300 |
| `resting_hr` | 25 | 220 |
| `respiratory_rate` | 5 | 40 |
| `spo2` | 50 | 100 |
| `skin_temp` | -20 | 60 |
| `percent_recorded` | 0 | 100 |

### CSV limits

| Constant | Value | Meaning |
|---|---:|---|
| `WHOOP_CSV_MAX_BYTES` | 512 KiB | Maximum accepted CSV payload bytes. |
| `WHOOP_CSV_MAX_ROWS` | 5000 | Maximum CSV rows before rejection. |
| Future date tolerance | Today + 1 day | Allows timezone-adjacent daily buckets; farther future dates reject. |

### Sync script constants

| Constant | Value | Meaning |
|---|---:|---|
| `MAX_ATTEMPTS` | 3 | Total scheduled runner attempts for retryable failures. |
| `MAX_RETRY_BACKOFF_SECONDS` | 30 | Retry delay cap. |
| Normal default | 2 days | Default scheduled normal window. |
| Normal range | 1-14 days | Allowed normal sync window. |
| Repair default | 7 days | Default repair window. |
| Repair range | 1-14 days | Allowed repair sync window. |
| Backfill default | 30 days | Default backfill window. |
| Backfill range | 30-90 days | Allowed backfill sync window. |

## 8. Integration Points

- Recommendation engine: WHOOP can downgrade or annotate `/api/dashboard`, `/api/next-workout`, and `/api/recommendation/smart`.
- Oura: WHOOP recovery bands are compared with Oura readiness bands to detect source conflicts. Conservative source wins when low/high bands disagree by more than one tier.
- Apple Health: WHOOP does not replace Apple Health load truth. Tests require load source to remain Apple Health when WHOOP modifies recovery/recommendation context.
- Open Wearables: `/api/whoop/callback` can complete an Open Wearables-managed WHOOP OAuth state when direct WHOOP state consumption fails. See [10-open-wearables-integration.md](10-open-wearables-integration.md).
- Backup/restore: WHOOP normalized facts participate in app backup but token material and raw upstream payloads do not. Import is destructive when `whoop_daily_facts` is present: it clears local WHOOP records/facts/sync runs first, then re-imports facts as synthetic `backup-<date>` recovery records. Backup import also takes the WHOOP mutation guard and returns 409 `whoop_sync_in_progress` if a WHOOP sync is running.
- Freshness: `/api/freshness` exposes WHOOP freshness for UI and dashboard confidence.

## 9. Permissions & Security

WHOOP app routes are owner-session routes. Mutation routes require the app’s CSRF mutation guard. The OAuth callback uses state binding instead of CSRF because it is a third-party redirect, but the browser session cookie must still be present.

Sensitive material rules:

- Client secret is read from macOS Keychain service `fitness-dashboard-whoop-client-secret`.
- Access and refresh tokens are stored in the protected material file, not in SQLite current columns, backups, logs, or UI status responses.
- Protected material defaults outside the repo and outside `DATA_DIR`; the filename is namespaced by database path.
- OAuth states are single-use, short-lived, and data-user-bound.
- Error text is redacted for access tokens, refresh tokens, client secrets, authorization codes, state values, and Bearer headers.
- OAuth responses and callback responses use no-store headers.
- CSV input is untrusted: size-capped, UTF-8 validated, metric-bounds validated, and normalized before persistence.

## 10. Business Rules

- WHOOP is a bounded modifier. It may make workouts more conservative but must not increase training load.
- Display-only WHOOP data never changes recommendation load. Display-only cases include stale data, missing data, pending score, unscorable score, calibrating state, and facts without actionable metrics.
- Fresh scored data is high confidence; aging data is medium confidence; display-only is low or none.
- Deload modifier changes recommendation toward recovery, volume scale to 0.8, RPE delta -1 with floor 5, estimated minutes down to at least 20, and target sets down but not below 1.
- Caution modifier downgrades one step, volume scale to 0.9, RPE delta -1 with floor 5, and similarly avoids increasing load.
- Modifier signatures make repeated application idempotent.
- Sleep rows use wake/end date for local day; non-sleep records prefer start date.
- WHOOP sleep naps are ignored.
- Official WHOOP sleep need components are used to compute sleep need gap when present; complete sleep should not be misread as unmet gap.
- CSV imports can drive local recommendations without OAuth when fresh and scored, but disconnected imported history is labeled CSV-only.
- Delete data is distinct from Disconnect. Disconnect removes authorization; Delete data removes local WHOOP records/import history/facts.
- Revoke failure during disconnect does not stop local token purge, but local protected-material deletion failure is surfaced.

## 11. Config & Environment

| Name | Default | Behavior when unset |
|---|---|---|
| `WHOOP_CLIENT_ID_FILE` | `.whoop-client-id` in repository root fallback | If no readable client ID exists, live OAuth status is `missing_config`. |
| Keychain `fitness-dashboard-whoop-client-secret` | None | If missing, live OAuth status is `missing_config`. |
| `WHOOP_SCOPES` | Direct WHOOP defaults | Defaults to `offline read:recovery read:cycles read:sleep read:workout`. |
| `WHOOP_PROTECTED_MATERIAL_DIR` | `~/Library/Application Support/Fitness Dashboard/secrets` | Protected token material is stored in the default directory. |
| `WHOOP_SYNC_BACKEND` | `app:_run_whoop_sync` | Scheduled script imports the app backend adapter. |
| `DATA_DIR` | App-level data path | WHOOP SQLite DB and sync lock live under this app data directory. |

## 12. Test Coverage

Existing coverage is broad:

- `tests/test_whoop_client.py`: config loading from Keychain, redaction, refresh, pagination, retry, revoke.
- `tests/test_whoop_oauth.py`: missing config, start/callback success, browser redirect, partial token rejection, invalid/cross-process state, disconnect, revoke failure, protected-material delete failure.
- `tests/test_whoop_store.py`: token material isolation, atomic cleanup, daily projection, pending score handling, rotation, state TTL/user binding, protected material path, clear data.
- `tests/test_whoop_freshness.py`: freshness classification, failed attempts after success, pending score freshness, reauth relevance, token non-leakage, optional/disconnected WHOOP confidence.
- `tests/test_whoop_import_sync_backup.py`: CSV projection/history, locks, CSV-only recommendations, date/timezone normalization, nap filtering, calibrating display-only, sleep need, payload/row/UTF-8/metric/future-date rejection, protected sync, token rotation, days validation, concurrent sync, terminal auth, retryable failures, backup export/import safety, delete data.
- `tests/test_whoop_recommendations.py`: deload/caution/fueling behavior, idempotency, conflict handling, stale/unscored display-only.
- `tests/test_whoop_source_conflicts.py`: source conflict detection, recommendation source truth, next-workout cache, disconnected facts.
- `tests/test_whoop_sync.py`: scheduled runner mode windows, retry behavior, redacted summaries, backend loading, help text.
- `tests/test_whoop_ui_contract.py`: UI surfaces, cache busting, Docker context exclusions, OAuth callback log safety, endpoint wiring, missing config modal, hash route, keyboard/focus path, scoped styles, conflict display, freshness merge, nutrition recomputation.

Notable gaps: no repo-owned launchd/cron installation test exists because scheduling is intentionally external; no live WHOOP sandbox contract test exists in this sanitized local repo.

## 13. Gaps & Issue Candidates

### IC-1: Define and install the automatic WHOOP ingestion schedule
- **Type:** Feature
- **Priority:** high
- **Where:** scripts/whoop_sync.py; /api/whoop/sync
- **Problem:** The sync runner has bounded modes and retry behavior, but the repo does not define how the owner machine installs or verifies launchd/cron scheduling. The script explicitly avoids creating automation, so automatic ingestion remains an operational gap.
- **Why it matters:** WHOOP can go stale unless the owner remembers manual sync, weakening the recovery signal.
- **Acceptance criteria:**
  - Document the supported scheduler and cadence for normal, repair, and backfill modes.
  - Provide a safe install/check command or owner-run instructions outside secrets.
  - Show stale/failed scheduled sync state in Settings without exposing tokens.
  - Add a test or smoke check for generated scheduler metadata if scheduler files are introduced.
- **Duplicate-of:** FIT-242

### IC-2: Complete token storage hardening audit for WHOOP material
- **Type:** Privacy
- **Priority:** high
- **Where:** whoop_store.py; whoop_client.py; /api/whoop/*
- **Problem:** Current code keeps access/refresh tokens in a protected 0600 material file outside SQLite and uses Keychain for the client secret, but FIT-261 tracks broader hardening of token-like material across integrations. WHOOP should have an explicit audit that legacy columns stay null, backups stay clean, logs stay redacted, and configured material directories cannot accidentally land inside public repo paths.
- **Why it matters:** OAuth material leakage would expose the owner’s health data authorization.
- **Acceptance criteria:**
  - Assert current SQLite token columns are empty after connect, refresh, disconnect, backup, and restore.
  - Warn or block protected material directories inside repo/public backup paths.
  - Verify redaction coverage for all WHOOP auth/code/state paths.
  - Document the remaining acceptable local-secret trust boundary.
- **Duplicate-of:** FIT-261

### IC-3: Return CSV import row outcomes instead of only total upserts
- **Type:** Data-contract
- **Priority:** medium
- **Where:** app.py:/api/whoop/import-csv
- **Problem:** Unsupported CSV record types are skipped, and the response reports only imported/upserted records. The owner cannot tell whether rows were ignored because they were unsupported, naps, duplicates, or malformed-but-skipped before normalization.
- **Why it matters:** A user can believe a WHOOP export was fully imported when meaningful rows were silently omitted.
- **Acceptance criteria:**
  - Response includes counts for parsed rows, imported rows, skipped unsupported rows, ignored naps, and duplicates/upserts.
  - UI shows a concise import summary after success.
  - Existing validation failures for invalid metrics, UTF-8, row cap, and future dates remain hard failures.
  - Add tests for mixed supported/unsupported CSV imports.
- **Duplicate-of:** none

### IC-4: Expose sync mode in the manual sync API contract
- **Type:** Improvement
- **Priority:** medium
- **Where:** app.py:/api/whoop/sync; scripts/whoop_sync.py; static/js/app.js
- **Problem:** The scheduled runner has explicit modes (`normal`, `repair`, `backfill`), but the HTTP route accepts only `days_back` and the frontend sends an ignored `trigger` field. This creates two nearby contracts for sync intent.
- **Why it matters:** Operators and future agents can misread manual sync as supporting scheduled mode semantics when it does not.
- **Acceptance criteria:**
  - Either document that `/api/whoop/sync` is manual-only or accept a validated `mode` field with the same safe bounds.
  - Remove or use the frontend `trigger` field.
  - Add tests for accepted/rejected route payloads.
  - Keep scheduled runner mode validation unchanged.
- **Duplicate-of:** FIT-242

### IC-5: Clarify tomorrow-date tolerance in CSV imports
- **Type:** Docs
- **Priority:** low
- **Where:** app.py `_validate_imported_whoop_local_date`; WHOOP modal import copy
- **Problem:** CSV dates later than tomorrow reject, but tomorrow is allowed. This appears to handle timezone-adjacent data, but the product behavior is not documented in the UI or top-level docs.
- **Why it matters:** A future-date row can look surprising in a daily recovery dashboard unless the owner understands the timezone tolerance.
- **Acceptance criteria:**
  - Document the one-day future tolerance as timezone protection or tighten it if unintended.
  - Add UI import help text or import-result warning when tomorrow-dated rows are accepted.
  - Keep tests covering farther-future rejection.
- **Duplicate-of:** none

### IC-6: Add a live WHOOP contract test strategy
- **Type:** Test
- **Priority:** low
- **Where:** whoop_client.py; scripts/whoop_sync.py
- **Problem:** The repo has strong local unit/contract tests but no live WHOOP sandbox check. That is appropriate for a sanitized public copy, but the operational path for verifying WHOOP API schema drift is [TBC].
- **Why it matters:** Upstream WHOOP response changes could break normalization without local tests noticing.
- **Acceptance criteria:**
  - Define an owner-only, secret-safe smoke test or recorded fixture refresh process.
  - Ensure recorded fixtures contain no tokens or raw private health payloads beyond approved sanitized shapes.
  - Document when to run the live check and how failures should be triaged.
- **Duplicate-of:** FIT-242
