# Open Wearables Integration — PRD

> **Sources:** README.md; docs/VISION.md; docs/PRD.md; docs/CURRENT_STATE.md; app.py; open_wearables_adapter.py; wearable_fact_store.py; recommendation_sources.py; templates/index.html; static/js/app.js; tests/test_open_wearables_adapter.py; tests/test_open_wearables_health_sync_redaction.py; tests/test_open_wearables_ui_contract.py; tests/test_recommendation_sources.py
> **Routes:** /api/health/sync; /api/open-wearables/status; /api/open-wearables/setup; /api/open-wearables/setup/bootstrap; /api/open-wearables/pair/<provider>; /api/open-wearables/mobile-invite/<provider>; /api/open-wearables/setup/check; /api/open-wearables/providers; /api/open-wearables/sync; /api/wearable-sources; /api/wearable-facts
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

## 1. Overview

Open Wearables is the generic wearable hub integration for Fitness Dashboard. It wraps a local or owner-approved Open Wearables sidecar in non-technical setup language: prepare a hub profile, choose a provider, sign in to cloud providers when real connector credentials exist, or generate phone-app invitation codes for Apple Health, Samsung Health, and Google Health Connect.

The integration is metadata-first and local-first. The app does not export raw health payloads through `/api/health/sync` or `/api/open-wearables/sync`. The bridge fetches provider summaries from the sidecar, stores only normalized coaching-safe daily facts, and returns counts, source metadata, stored fact counts, and stable error codes. Raw samples, token names, hub secrets, and exception strings are not user-facing API payloads.

Open Wearables does not replace the direct WHOOP integration as the durable WHOOP source of truth. It can offer a WHOOP provider action when Open Wearables has real connector credentials, and the direct WHOOP callback can complete an Open Wearables WHOOP OAuth state as a fallback. Direct WHOOP remains covered in [09-whoop-integration.md](09-whoop-integration.md).

The current design splits normal setup from diagnostics. The owner sees "Add a wearable", "Prepare pairing", provider buttons, and phone invitation code instructions. Advanced fields for hub URL, portal URL, username, hub secret, and user mapping are present but secondary. Provider readiness is intentionally conservative so placeholder credentials, missing hub catalog data, unsafe remote URLs, loopback phone invite URLs, and stale restart requirements do not present as successful connections.

## 2. User-Facing Surfaces

### Settings row

The Settings page includes an "Open Wearables" row with status chip, Setup/Add device button, Sync button, and detail rows. Details include Hub, Providers, Policy (`Normalized facts only · coaching stays local`), and Attention when blocked or errored.

The Setup button text changes between "Add device" and "Manage devices" depending on connection status. Sync is disabled unless the hub is connected/ready enough to sync or saved setup indicates a mapped hub/provider state.

### Add a wearable modal

The modal presents a non-technical setup flow:

- Step 1: Person/profile preparation.
- Step 2: Device/provider choice.
- Step 3: Connection check/sync readiness.

Primary controls are "Prepare pairing" and "Check connection". Provider buttons are shown for Garmin, Oura, WHOOP, Suunto, Polar, Ultrahuman, Strava, Fitbit, Apple Health, Samsung Health, and Google Health Connect. Cloud provider buttons open an Open Wearables authorization URL only when the connector is ready. Phone health provider buttons create a one-time invitation code.

The modal includes a mobile invite panel with server address, one-time code, expiry, and provider label. It also includes an advanced diagnostics panel with Hub API URL, Pairing portal URL, Hub username, Hub secret, User mapping, Open hub admin, Copy hub link, Save advanced, and GitHub reference actions.

### Wearable sources and facts

`/api/wearable-sources` powers a combined source list for app freshness/recommendation proof. `/api/wearable-facts` exposes normalized, profile-scoped facts for recommendation diagnostics. These routes are the product-safe way to inspect what the app will use for coaching.

### Recommendation surfaces

Open Wearables facts can conservatively downgrade dashboard, next workout, or smart recommendation output. The integration is source-aware and profile-scoped. Generic Open Wearables providers are listed as recommendation sources, while stale legacy summaries are hidden from active recommendation proof.

## 3. Field Inventory

### Open Wearables local configuration

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `base_url` | URL | Yes once configured | `http://localhost:8000` | Local HTTP allowed; remote must be HTTPS and allowlisted | Open Wearables API base. |
| `portal_url` | URL/null | No | From config or sidecar `FRONTEND_URL` | Same safe URL rules as base URL | Human-facing pairing/admin portal. |
| `username` | String | Yes for hub login | Env/local config | Max 128 chars | Hub login username. |
| `password` | Secret string | Yes for hub login | Env/local config | Max 512 chars; not echoed in API responses | Hub login secret. |
| `user_id` | String | Yes after mapping | Profile mapping or legacy env | Max 128 chars; verified against current profile on manual save | Open Wearables user id for the owner profile. |
| `profiles` | Object | No | `{}` | Profile-key map | Stores profile-scoped hub user mappings. |
| `sidecar_env_path` | Path/null | No | `~/open-wearables/backend/config/.env` for managed detection only | Readable env file when bootstrapping | Source of local sidecar admin credentials and connector env values. |
| `managed_connector_restart_required` | Boolean | No | false | Persisted flag | Blocks provider pairing until the sidecar has restarted after connector env changes. |

The local config file is `DATA_DIR/open_wearables_config.json` and is saved chmod 0600.

### Setup public config

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `base_url` | URL | Yes | Local config/env/default | Redacted to base path for public status | Hub API endpoint shown in diagnostics. |
| `username` | String/null | No | Config/env | Not secret | Shows whether login is configured. |
| `user_id` | String/null | No | Profile mapping/env | Profile-scoped | Current profile's hub mapping. |
| `profile_key` | String | Yes | Current data user id or `1` | Sanitized to letters, numbers, `_`, `.`, `:`, `-`; others become `-` | Prevents cross-profile source leakage. |
| `portal_url` | URL/null | No | Config/env | Safe URL validation | Hub portal link. |
| `pairing_url` | URL/null | No | Derived | Safe URL validation | Link used for pairing/admin navigation. |
| `password_configured` | Boolean | Yes | Derived | Does not expose value | Indicates hub secret presence. |
| `hub_account_ready` | Boolean | Yes | Derived | username + password | Whether local hub login can be attempted. |
| `user_mapped` | Boolean | Yes | Derived | profile has user id | Whether profile has an Open Wearables user. |
| `bootstrap_available` | Boolean | Yes | Derived | local managed sidecar only | Whether "Prepare pairing" can bootstrap automatically. |
| `managed_connector_restart_required` | Boolean | Yes | Derived | Config flag | Whether sidecar restart blocks cloud pairing. |
| `provider_setup_ready` | Boolean | Yes | Derived | Any provider action ready | Whether provider choice can proceed. |
| `provider_actions` | Array | Yes | [] | See provider action fields | What the UI should show for each provider. |
| `config_file` | String | Yes | Basename only | No full secret path | Diagnostic local config filename. |

### Provider action

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `provider` | Enum | Yes | None | See provider enum | Stable provider id. |
| `label` | String | Yes | Provider label | Catalog or built-in label | Button label. |
| `kind` | Enum | Yes | `cloud` or `sdk` | Built-in provider kind | Cloud OAuth vs phone SDK invite flow. |
| `enabled` | Boolean | Yes | Derived | True only when action should be available | Whether the user can proceed. |
| `url` | URL/string | No | "" | Internal app pair route when ready | Pair endpoint for cloud providers. |
| `reason` | Enum/string | No | "" | See reason enum | Why a provider is unavailable. |
| `icon_url` | URL/null | No | null | From hub catalog | Provider icon if available. |
| `live_sync_mode` | String/null | No | null | From hub catalog | Hub-declared sync mode. |
| `managed_credentials_ready` | Boolean | Yes | Derived | True for unmanaged or real sidecar creds | Whether cloud app credentials are present. |

### Provider status

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `provider_id` | String | Yes | From hub row | Normalized from `provider`, `id`, or record id | Provider identity. |
| `label` | String | Yes | Provider id | From hub row | Human label. |
| `state` | String | Yes | `unknown` | From hub status | Connected/readiness state. |
| `capabilities` | Object | Yes | Booleans false | `metrics`, `workouts`, `history`, `webhooks`, `sync` | What the provider supports. |
| `last_sync_at` | ISO datetime/null | No | null | From hub row | Last provider sync. |
| `stale` | Boolean | No | false | From hub row | Whether provider data is stale. |
| `error_code` | String/null | No | null | Redacted/stable if present | Provider-specific safe error. |

### Sync metadata response

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `status` | Enum | Yes | `success` or `error` | Route-level status | Whether metadata sync completed. |
| `source` | String | Yes | `open_wearables` | Constant | Sync source. |
| `fetched_at` | ISO datetime | Yes | Now if absent | Server timestamp | When sidecar fetch was attempted. |
| `counts.sleep` | Integer | No | omitted when unavailable | Count only, not payload | Number of sleep records seen. |
| `counts.workouts` | Integer | No | omitted when unavailable | Count only | Number of workout records seen. |
| `counts.activity_summary` | Integer | No | omitted when unavailable | Count only | Number of activity summaries seen. |
| `errors` | Object | Yes | `{}` | Stable public codes | Sync issues without raw exception detail. |
| `facts_upserted` | Integer | Only `/api/open-wearables/sync` | 0 | Count of normalized local facts | How many safe facts were stored. |

### Normalized wearable fact

| Field | Type | Required | Default | Validation | Business meaning |
|---|---:|---:|---|---|---|
| `profile_key` | String | Yes | Current profile | Sanitized | Owner/profile boundary. |
| `date` | Date | Yes | None | Daily date | Fact bucket. |
| `provider_id` | String | Yes | `open_wearables` for bridge facts | Provider id | Source/provider key. |
| `source_label` | String | Yes | Provider label | Plain text | Display label. |
| `metric` | Enum/string | Yes | None | Stored metric key | Coaching-safe metric. |
| `value_json` | JSON string | Yes | None | Cannot include forbidden raw/secret fields | Stored metric value. |
| `unit` | String/null | No | null | Unit label | Count, bpm, minutes, etc. |
| `band` | String/null | No | null | Optional | Product tier for display/recommendation. |
| `confidence` | Enum | No | `medium` for OW bridge facts | Local enum | Recommendation confidence. |
| `freshness` | Enum | No | From sync | `fresh`, `aging`, `stale`, etc. | Whether fact can affect recommendations. |
| `conflict_state` | String/null | No | null | Optional | Source conflict marker. |
| `used_for_recommendation` | Boolean | Yes | Derived | True for safe usable facts | Whether recommendation engine can consume it. |
| `updated_at` | ISO datetime | Yes | Now | Server timestamp | Last local write. |

Open Wearables bridge currently stores these metrics when present: `steps`, `resting_heart_rate`, `active_minutes`, `sleep_duration`, and `sleep_avg_heart_rate`.

## 4. Interactions & Flows

### Load Open Wearables status

Trigger -> Settings render, wearable source load, freshness calculation, or setup modal open.

Behavior -> Backend builds public status from configured base URL, profile mapping, provider statuses, and provider actions. The UI renders a chip, details, provider choices, and attention messages.

Validation -> Remote hub URLs must be HTTPS and host-allowlisted. Public status redacts URL paths and does not expose passwords or token values.

API -> `GET /api/open-wearables/status`, `GET /api/open-wearables/setup`, `GET /api/wearable-sources`.

Success -> UI can distinguish missing setup, blocked unsafe config, waiting for provider, connected, stale, or error.

Failure -> Stable error codes are shown; raw exception text is not returned.

### Bootstrap local hub profile

Trigger -> Owner clicks Prepare pairing, or POST pairing/invite route attempts setup while missing hub user/auth.

Behavior -> Backend reads the sidecar env file, requires local loopback hub for automatic bootstrap, logs in as sidecar admin, finds or creates a hub user with external id `fitness-dashboard-user-{profile_key}`, seeds managed connector credentials when supported, saves profile mapping, and returns provider actions.

Validation -> Sidecar env must contain `ADMIN_EMAIL` and `ADMIN_PASSWORD`. Automatic bootstrap refuses remote sidecar credentials. Existing users must match the exact expected external user id. Config save failures are reported without leaking secrets.

API -> `POST /api/open-wearables/setup/bootstrap`.

Success -> Response status `ready`, bootstrap info, public config, Open Wearables status, and provider check.

Failure -> Response status `blocked`, stable error code/message, current public config/status. Missing/unreadable sidecar env, missing sidecar admin credentials, remote hub, unsafe base URL, and config save failure are separate states; login and user-mapping failures collapse into a single `hub_bootstrap_failed` code.

### Save advanced setup

Trigger -> Owner expands diagnostics and saves hub API URL, portal URL, username, hub secret, or user mapping.

Behavior -> Backend validates URLs and lengths, checks host-change rules, verifies supplied user mapping against the current profile where needed, saves config, and refreshes provider check.

Validation -> `base_url` max 256 chars; `username` max 128; `user_id` max 128; `portal_url` max 256; `password` max 512. Host changes require fresh secret and user mapping. Client-supplied remote allowlists are ignored. Unsafe pairing portal URLs are blocked.

API -> `POST /api/open-wearables/setup`.

Success -> Response status `saved`, public config, public Open Wearables status, and provider check. Secret value is not echoed.

Failure -> Validation errors return stable codes such as credential required, user mapping required, unsafe URL, or user mapping verification failed.

### Check connection

Trigger -> Owner clicks Check connection.

Behavior -> Backend validates current or supplied base URL, optionally clears restart-required when hub catalog proves the restart is complete, retrieves provider catalog/status, and returns attention or ok.

Validation -> Same safe URL rules. Provider check does not seed sidecar credentials on read.

API -> `POST /api/open-wearables/setup/check`.

Success -> Status is `ok` when connected, otherwise `attention` with provider check details.

Failure -> Stable blocked/error state with redacted message.

### Pair cloud provider

Trigger -> Owner clicks a ready cloud provider such as Garmin, Oura, WHOOP, Suunto, Polar, Ultrahuman, Strava, or Fitbit.

Behavior -> UI posts to `/api/open-wearables/pair/<provider>`. Backend bootstraps if POST and setup is missing, validates provider catalog, checks sidecar credential readiness, calls the hub authorization endpoint, and returns or redirects to the authorization URL.

Validation -> Unknown provider rejects. SDK/phone providers reject from the cloud pair route. Managed sidecars require non-placeholder connector credentials. Restart-required blocks pairing until the hub has restarted. Placeholder values containing blank, `your-`, `public-client-id`, or `private-secret-id` are not ready.

API -> `GET/POST /api/open-wearables/pair/<provider>`.

Success -> POST returns `authorization_url`; GET redirects to that URL.

Failure -> Stable reasons include `prepare_profile`, `hub_restart_needed`, `provider_catalog_unavailable`, `provider_not_ready`, `provider_disabled`, `provider_app_needed`, `sdk_provider`, and unsafe base URL errors.

### Create phone invite

Trigger -> Owner clicks Apple Health, Samsung Health, or Google Health Connect.

Behavior -> Backend ensures a mapped user and valid public server URL, requests an invitation code from the hub, and returns code, server URL, provider label, and expiry.

Validation -> Only SDK providers can use this route. Loopback server URLs are blocked because phones cannot use them. Cloud providers reject. Missing code from hub is an error.

API -> `POST /api/open-wearables/mobile-invite/<provider>`.

Success -> UI shows server address and one-time code in the mobile invite panel.

Failure -> Stable codes include `cloud_provider`, `mobile_invite_not_ready`, `mobile_invite_failed`, loopback/public URL blocks, and missing setup errors.

### Sync metadata and facts

Trigger -> Owner clicks Sync or another route requests health sync.

Behavior -> Backend logs into Open Wearables, fetches the last seven days of sleep, workouts, and activity summaries, counts records, stores normalized safe facts for `/api/open-wearables/sync`, and returns metadata only. Fact storage is intentionally coarse: at most the latest activity-summary day and latest sleep day are persisted as recommendation facts.

Validation -> `/api/health/sync` returns metadata counts but does not store facts. It still refreshes the in-memory per-profile recommendation marker cache used by recommendation inputs. `/api/open-wearables/sync` stores selected safe facts and returns `facts_upserted`. Forbidden raw/secret fields are rejected by the fact store if they enter a fact payload.

API -> `POST /api/health/sync`; `POST /api/open-wearables/sync`.

Success -> Counts and stable source metadata return; no raw health payloads return.

Failure -> Route-level exception returns `open_wearables_sync_failed`; auth/config/provider failures map to stable error codes.

### Apply recommendation guard

Trigger -> Dashboard, next workout, or smart recommendation generation.

Behavior -> Backend reads profile-scoped Open Wearables facts and sources. Low sleep duration or high active minutes can downgrade a recommendation once. Missing/stale facts are display-only.

Validation -> Facts must be normalized and profile-scoped. Guard never hardens or increases workout load.

API -> Consumed by `/api/dashboard`, `/api/next-workout`, `/api/recommendation/smart`, `/api/wearable-facts`, and source routes.

Success -> Recommendation source proof includes Open Wearables provider details and conservative modifier detail when applied.

Failure -> Missing or stale Open Wearables data remains display-only and does not block recommendations.

## 5. API Endpoints

| Method | Path | Auth | Trigger | Key params | Response shape | Real/Mock |
|---|---|---|---|---|---|---|
| POST | `/api/health/sync` | Owner session + CSRF | Generic health sync | None | Metadata counts, stable errors | Real sidecar metadata; no fact storage |
| GET | `/api/open-wearables/status` | Owner session | Status/freshness | None | Public status | Real local config + sidecar probe |
| GET | `/api/open-wearables/setup` | Owner session | Open setup modal | None | Public config, status, provider actions | Real local state |
| POST | `/api/open-wearables/setup` | Owner session + CSRF | Save diagnostics | base URL, portal URL, username, password, user id | Saved config/status/provider check | Real local config |
| POST | `/api/open-wearables/setup/bootstrap` | Owner session + CSRF | Prepare pairing | None | Bootstrap result, config, status | Real local sidecar bootstrap |
| GET | `/api/open-wearables/pair/<provider>` | Owner session | Browser redirect | Provider id | Redirect or blocked response | Real sidecar OAuth |
| POST | `/api/open-wearables/pair/<provider>` | Owner session + CSRF | Provider button | Provider id | Authorization URL or blocked status | Real sidecar OAuth |
| POST | `/api/open-wearables/mobile-invite/<provider>` | Owner session + CSRF | Phone provider button | SDK provider id | Invitation code/server/expiry | Real sidecar invite |
| POST | `/api/open-wearables/setup/check` | Owner session + CSRF | Check connection | Optional base URL | Provider check/status | Real sidecar probe |
| GET | `/api/open-wearables/providers` | Owner session | Provider list | None | Public provider statuses | Real sidecar probe |
| POST | `/api/open-wearables/sync` | Owner session + CSRF | Sync button | None | Metadata + facts upserted | Real sidecar metadata + local facts |
| GET | `/api/wearable-sources` | Owner session | Source proof | None | Stored sources + freshness | Real local fact store |
| GET | `/api/wearable-facts` | Owner session | Diagnostics/recommendation proof | `limit` 1-100, default 30 | Profile-scoped facts | Real local fact store |

Endpoint detail:

- `/api/health/sync` and `/api/open-wearables/sync` are metadata-only responses. They explicitly do not export raw health payloads.
- The Open Wearables token cache reuses hub auth until expiry minus 30 seconds. Expiry comes from `expires_in`, JWT `exp`, or a 3300 second default.
- Open Wearables hub requests retry once after 401 by clearing the cached token and logging in again.
- Remote base URLs require HTTPS and inclusion in `OW_ALLOWED_HOSTS`.
- Provider catalog is read from `/api/v1/oauth/providers?enabled_only=true`.
- Data sync reads `/events/sleep`, `/events/workouts`, and `/summaries/activity` for today minus six days through today.

## 6. Data Model & Persistence

### Local setup config

`DATA_DIR/open_wearables_config.json` stores local hub connection settings. It can include `base_url`, `portal_url`, `username`, `password`, `profiles`, legacy `user_id`, optional `sidecar_env_path`, and `managed_connector_restart_required`.

The API never echoes `password`. The file is local and chmod 0600, but it is still plaintext local config; see issue candidates.

### Profile mapping

Profile key is derived from the current Fitness Dashboard data user id. Invalid filename/key characters are converted to hyphens. Mappings are stored under `profiles[profile_key]` with:

- `user_id`
- `external_user_id`
- `mapped_at`

Expected external user id is `fitness-dashboard-user-{profile_key}`. Manual mapping and bootstrap both verify exact profile ownership. Profile `1` can use legacy top-level `OW_USER_ID`/`user_id`; current code prefers profile mapping before legacy.

When mapping changes, the app clears Open Wearables recommendation marker cache, deletes stored Open Wearables facts for that profile/provider, and invalidates the last recommendation.

### Wearable fact SQLite store

Normalized wearable facts and source status live in `DATA_DIR/wearable_facts.sqlite3`.

`wearable_daily_facts`:

- Primary key: `(profile_key, date, provider_id, metric)`.
- Fields: `profile_key`, `date`, `provider_id`, `source_label`, `metric`, `value_json`, `unit`, `band`, `confidence`, `freshness`, `conflict_state`, `used_for_recommendation`, `updated_at`.

`wearable_sources`:

- Primary key: `(profile_key, provider_id)`.
- Fields: `profile_key`, `provider_id`, `label`, `status`, `last_data_point`, `last_sync_attempt`, `capabilities_json`, `used_for_recommendation`, `updated_at`.

Migration logic upgrades legacy non-profile-scoped tables by preserving old rows under profile `1`. This avoids losing previous facts while preventing future cross-profile leakage.

### Stored Open Wearables bridge facts

`/api/open-wearables/sync` stores source `provider_id=open_wearables`, label `Open Wearables`, status `fresh` or `error`, last data point from fetch date, and capabilities `metrics`, `workouts`, `history`, and `sync`.

Current extraction stores:

- From activity summary: `steps`, `resting_heart_rate`, `active_minutes`.
- From sleep summary: `sleep_duration`, `sleep_avg_heart_rate`.

Extraction details depend on sidecar payload keys and are [TBC] beyond the normalized fields observed in code.

### Forbidden fact fields

The fact store rejects any nested fact payload containing these field names or patterns:

- `authorization`
- `access_token`
- `refresh_token`
- `token`
- `password`
- `secret`
- `raw`
- `payload`
- `samples`
- `records`
- `user_id`
- Any key ending in `_token`

## 7. Enums & Constants

### Environment variables

| Name | Default | Meaning |
|---|---|---|
| `OW_BASE_URL` | `http://localhost:8000` | Open Wearables API base. |
| `OW_PORTAL_URL` | None | Optional human portal/pairing URL. |
| `OW_USERNAME` | None | Hub login username. |
| `OW_PASSWORD` | None | Hub login secret. |
| `OW_USER_ID` | None | Legacy/default profile user mapping. |
| `OW_SIDECAR_ENV_PATH` | `~/open-wearables/backend/config/.env` | Managed sidecar env file path. |
| `OW_ALLOWED_HOSTS` | None | Comma/list of approved remote hub hosts. |

### Providers

| Provider | Label | Kind | Behavior |
|---|---|---|---|
| `garmin` | Garmin | cloud | Opens hub OAuth when catalog and credentials are ready. |
| `oura` | Oura | cloud | Opens hub OAuth when catalog and credentials are ready. |
| `whoop` | WHOOP | cloud | Opens hub OAuth when catalog and credentials are ready; managed seeding supported. |
| `suunto` | Suunto | cloud | Opens hub OAuth when catalog and credentials are ready. |
| `polar` | Polar | cloud | Opens hub OAuth when catalog and credentials are ready. |
| `ultrahuman` | Ultrahuman | cloud | Opens hub OAuth when catalog and credentials are ready. |
| `strava` | Strava | cloud | Opens hub OAuth when catalog and credentials are ready. |
| `fitbit` | Fitbit | cloud | Opens hub OAuth when catalog and credentials are ready. |
| `apple` | Apple Health | sdk | Generates phone-app invitation code. |
| `samsung` | Samsung Health | sdk | Generates phone-app invitation code. |
| `google` | Google Health Connect | sdk | Generates phone-app invitation code. |

### Provider credential keys

| Provider | Required sidecar env keys |
|---|---|
| `whoop` | `WHOOP_CLIENT_ID`, `WHOOP_CLIENT_SECRET` |
| `oura` | `OURA_CLIENT_ID`, `OURA_CLIENT_SECRET` |
| `garmin` | `GARMIN_CLIENT_ID`, `GARMIN_CLIENT_SECRET` |
| `strava` | `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` |
| `fitbit` | `FITBIT_CLIENT_ID`, `FITBIT_CLIENT_SECRET` |
| `polar` | `POLAR_CLIENT_ID`, `POLAR_CLIENT_SECRET` |
| `suunto` | `SUUNTO_CLIENT_ID`, `SUUNTO_CLIENT_SECRET` |
| `ultrahuman` | `ULTRAHUMAN_CLIENT_ID`, `ULTRAHUMAN_CLIENT_SECRET` |

Exact non-WHOOP env key names come from the app provider credential mapping.

### Provider action reasons

| Value | Meaning |
|---|---|
| `prepare_profile` | Hub user/profile is not ready; run bootstrap first. |
| `hub_restart_needed` | Managed connector env changed and sidecar restart has not been observed. |
| `sdk_provider` | Provider uses phone SDK invite, not cloud OAuth. |
| `provider_catalog_unavailable` | Hub provider catalog could not be fetched. |
| `provider_not_ready` | Hub does not expose a ready cloud authorization API for this provider. |
| `provider_disabled` | Provider exists but is disabled in the hub catalog. |
| `provider_app_needed` | Cloud connector credentials are missing or placeholders. |
| Empty string | Provider action is ready. |

### Setup/UI states

| Value | Meaning |
|---|---|
| `checking` | UI is loading status/check results. |
| `needs_hub_account` | Hub credentials or local account are missing. |
| `needs_person` | Hub account exists but profile user mapping is missing. |
| `needs_provider_credentials` | Cloud provider app credentials are missing/placeholders. |
| `setup_hint: "provider_credentials_missing"` | Public status hint when auth and mapping are ready but no provider actions are available; the UI derives `needs_provider_credentials` from this field. |
| `hub_restart_needed` | Sidecar restart required before pairing. |
| `ready_to_choose_provider` | Profile ready; owner can select provider. |
| `pairing_provider` | OAuth pair flow is being opened. |
| `checking_connection` | Provider check is running. |
| `ready_to_sync` | Provider connection exists and sync can run. |
| `connected` | At least one healthy provider is connected/active/ready/ok/enabled. |
| `syncing` | Sync in progress. |
| `blocked` | Unsafe config or required setup block. |
| `error` | Last action failed. |

### Stable public error codes

| Value | Meaning |
|---|---|
| `open_wearables_auth_error` | Hub authentication/token failure. |
| `open_wearables_config_error` | Missing or invalid hub configuration. |
| `open_wearables_sync_error` | Provider data sync failed. |
| `open_wearables_sync_failed` | Route-level sync exception. |
| `base_not_allowed` | Hub catalog fetch blocked because the saved base URL fails validation on pair/provider-action paths. |
| `invalid_url` | Setup-save/check URL validation failed because the base URL is malformed. |
| `remote_requires_tls` | Remote hub URL is not HTTPS. |
| `remote_host_not_allowed` | Remote hub host is not in the allowlist. |
| `public_hub_url_required` | A public hub URL is required for the requested operation. |
| `missing_user_mapping` | Current profile has no verified hub user mapping. |
| `hub_auth_failed` | Hub authentication failed. |
| `provider_not_supported` | Requested provider is not supported by the hub/app mapping. |
| `provider_authorization_failed` | Provider authorization failed. |
| `credential_required_for_host_change` | Host change requires credentials. |
| `user_mapping_required_for_host_change` | Host change requires verified user mapping. |
| `credential_required_for_user_mapping` | Mapping change requires credentials. |
| `user_mapping_verification_failed` | User mapping verification failed. |
| `config_save_failed` | Open Wearables config save failed. |
| `sidecar_env_missing`, `sidecar_env_unreadable`, `sidecar_admin_missing`, `remote_hub_requires_manual_credentials`, `hub_bootstrap_failed` | Setup/bootstrap failures from sidecar env or hub bootstrap. |
| `open_wearables_provider_check_failed`, `open_wearables_provider_stale`, `open_wearables_provider_inactive` | Provider check/freshness failures. |
| `provider_catalog_unavailable` | Hub provider catalog unavailable. |
| `provider_not_ready` | Provider cannot start OAuth yet. |
| `provider_disabled` | Provider disabled in hub. |
| `provider_app_needed` | Real connector credentials missing. |
| `hub_restart_needed` | Restart required after managed env update. |
| `sdk_provider` | Cloud pair attempted for SDK provider. |
| `cloud_provider` | Mobile invite attempted for cloud provider. |
| `mobile_invite_not_ready` | Hub refused invite as not ready. |
| `mobile_invite_failed` | Invite request failed generally. |

### Recommendation thresholds

| Fact | Threshold | Effect |
|---|---:|---|
| `sleep_duration` | `< 360 minutes` | Apply sleep caution downgrade. |
| `active_minutes` | `>= 90 minutes` | Apply activity caution downgrade. |

The Open Wearables guard never hardens/increases a recommendation.

## 8. Integration Points

- WHOOP direct integration: Open Wearables can manage a WHOOP OAuth provider, but direct WHOOP remains the durable source for WHOOP normalized facts. The direct WHOOP callback can fall back to Open Wearables state completion.
- Recommendation sources: `recommendation_sources.py` reports Open Wearables as display-only when missing/stale and as a bounded modifier when fresh facts exist.
- Dashboard/next workout/smart recommendation: Open Wearables facts can downgrade recommendations through `_apply_open_wearables_recommendation_guard`.
- Freshness and source proof: `/api/wearable-sources` combines stored source state with WHOOP and Open Wearables freshness.
- Sidecar admin/hub: bootstrap uses sidecar env admin credentials and Open Wearables API endpoints. This is a real local integration, not fixture data.
- Phone apps: Apple Health, Samsung Health, and Google Health Connect use invitation codes intended for phone app pairing.

## 9. Permissions & Security

Open Wearables routes are owner-session routes. Mutating routes require the app's CSRF guard. OAuth redirects to external providers are allowed only after backend URL/provider validation.

Security rules:

- Remote Open Wearables base URLs require HTTPS and `OW_ALLOWED_HOSTS`; local loopback HTTP is allowed.
- Public status redacts URL paths and never returns hub passwords.
- Client-supplied remote allowlists are ignored.
- Host changes require a fresh credential and verified user mapping.
- Automatic bootstrap refuses remote hub sidecar credentials.
- User mappings must match exact expected external user id for the current profile.
- Managed sidecar env backups are restricted and connector env writes use restricted permissions.
- Placeholder connector credentials are treated as not ready.
- Mobile invite blocks loopback server URLs because a phone cannot use the owner Mac's loopback address.
- Fact store rejects raw payload, sample, record, token, password, secret, authorization, and user-id fields.

Privacy boundary: Open Wearables sync responses return metadata and counts only. They are not raw health export APIs.

## 10. Business Rules

- Open Wearables is the main generic wearable hub, but not the direct WHOOP source of truth.
- Setup must be truthful. A provider button should not claim success when hub catalog, connector credentials, restart state, or phone invite prerequisites are missing.
- Profile scoping is mandatory. The current data user maps to one Open Wearables user, and facts/sources are queried by profile key.
- Mapping changes clear profile-scoped Open Wearables facts and recommendation marker cache to avoid stale cross-user proof.
- Local loopback hub can bootstrap automatically from sidecar env. Remote hubs require manual safe config because sidecar admin secrets must not be reused remotely.
- Cloud provider OAuth requires real connector credentials and a hub catalog entry with cloud API support.
- SDK/phone providers do not use the cloud OAuth pair route; they use invitation codes.
- `/api/health/sync` is a metadata check, not a durable storage operation.
- `/api/open-wearables/sync` is the storage bridge and writes only normalized safe facts.
- Sync reads a seven-day date window ending today.
- Open Wearables recommendation facts can downgrade once but never increase load.
- Stale or missing Open Wearables data is display-only.

## 11. Config & Environment

| Name | Default | Behavior when unset |
|---|---|---|
| `OW_BASE_URL` | `http://localhost:8000` | Local loopback hub is assumed. |
| `OW_USERNAME` | None | Hub login unavailable unless local config supplies username. |
| `OW_PASSWORD` | None | Hub login unavailable unless local config supplies password. |
| `OW_USER_ID` | None | User mapping must be bootstrapped or saved; legacy value used only for default profile fallback. |
| `OW_PORTAL_URL` | None | Pairing/admin link derives from base URL or remains unset. |
| `OW_SIDECAR_ENV_PATH` | `~/open-wearables/backend/config/.env` | Managed sidecar detection can read default path; default path is not persisted unless explicitly configured. |
| `OW_ALLOWED_HOSTS` | None | Remote non-loopback hubs are blocked unless host is listed. |
| `DATA_DIR` | App-level data path | Stores local config and `wearable_facts.sqlite3`. |

## 12. Test Coverage

Existing coverage is broad:

- `tests/test_open_wearables_adapter.py`: remote URL validation, redacted base URL, missing config, generic provider status/capability parsing, provider payload parsing, connected/waiting status rules.
- `tests/test_open_wearables_health_sync_redaction.py`: metadata-only health sync, stable exception responses, remote allowlist blocking, provider route probes, setup check attention states, setup save secret redaction/preservation, sidecar bootstrap, profile mapping, exact external user verification, remap cache/fact clearing, profile-scoped sync, managed WHOOP seeding, restart-required handling, provider actions, pair flow errors/success, WHOOP callback fallback, mobile invite safety, IPv6/public URL behavior, and saved allowed hosts.
- `tests/test_open_wearables_ui_contract.py`: Settings hub controls, JavaScript wiring for Open Wearables sources/history, AI fact query safety, history source merge behavior.
- `tests/test_recommendation_sources.py`: Open Wearables display-only source state, generic provider listing, normalized-fact-only source proof, smart/dashboard/next-workout downgrade behavior, current-profile fact filtering, and modifier non-hardening.

Notable gaps: no live sidecar integration test is run by this assignment, no app-run visual QA was performed, and actual connector credential setup for non-WHOOP providers is [TBC] from code only.

## 13. Gaps & Issue Candidates

### IC-1: Make unavailable provider actions impossible to mis-click
- **Type:** Bug
- **Priority:** high
- **Where:** static/js/app.js provider action rendering; /api/open-wearables/pair/<provider>
- **Problem:** Backend provider actions distinguish ready, SDK, restart-needed, credential-missing, disabled, and catalog-unavailable states, but the UI still renders all provider buttons and handles unavailable clicks with explanatory messages. This can feel like a broken pairing flow instead of a clear setup requirement.
- **Why it matters:** The owner can waste time clicking provider actions that cannot succeed yet.
- **Acceptance criteria:**
  - Unavailable providers render with explicit disabled semantics and a single clear reason.
  - Ready cloud providers, SDK invite providers, and owner-setup-required providers have visually distinct actions.
  - Keyboard and screen-reader state matches click behavior.
  - Add UI contract tests for disabled/unavailable provider actions.
- **Duplicate-of:** FIT-253

### IC-2: Finalize Open Wearables as the main wearable hub spec
- **Type:** Feature
- **Priority:** high
- **Where:** docs/PRD.md; app.py Open Wearables routes; Settings Open Wearables modal
- **Problem:** The code now provides a broad hub wrapper, provider action list, profile mapping, metadata sync, and normalized facts, but the long-term product spec for "Open Wearables as the main wearable hub" is still tracked separately. Remaining decisions include how direct provider integrations coexist, what provider readiness means in product language, and which facts graduate into first-class coaching inputs.
- **Why it matters:** Without a finished hub spec, future agents may duplicate direct integrations or over-promote metadata-only sync into raw-data ownership.
- **Acceptance criteria:**
  - Define source-of-truth ownership between direct integrations and Open Wearables by provider.
  - Define provider readiness, connected, stale, and blocked states in product terms.
  - Define which normalized facts are eligible for coaching and which stay display-only.
  - Link setup UI, sync routes, and recommendation source proof to the same contract.
- **Duplicate-of:** FIT-245

### IC-3: Move Open Wearables hub secret out of local plaintext config
- **Type:** Privacy
- **Priority:** high
- **Where:** DATA_DIR/open_wearables_config.json; app.py /api/open-wearables/setup
- **Problem:** The setup API does not echo the hub password and saves config chmod 0600, but the local config can still contain the Open Wearables hub secret in plaintext. Existing tests prove redaction and local preservation, not stronger secret storage.
- **Why it matters:** A local config leak would expose the hub account and connected wearable metadata.
- **Acceptance criteria:**
  - Store hub password in Keychain or another protected material store.
  - Migrate existing plaintext config safely without logging the secret.
  - Preserve host-change verification behavior.
  - Add backup/export tests proving the secret is not included.
- **Duplicate-of:** FIT-261

### IC-4: Separate metadata check naming from durable fact sync
- **Type:** Data-contract
- **Priority:** medium
- **Where:** /api/health/sync; /api/open-wearables/sync
- **Problem:** `/api/health/sync` sounds like a durable sync but returns metadata counts only and does not store facts. `/api/open-wearables/sync` performs the durable normalized fact write. The behavior is safe, but the route naming can mislead future callers.
- **Why it matters:** A caller may use `/api/health/sync` and assume recommendation facts were refreshed when they were not.
- **Acceptance criteria:**
  - Document `/api/health/sync` as metadata-only in route docs and UI-facing developer docs.
  - Consider aliasing a clearer route name such as `/api/open-wearables/check-sync` while preserving compatibility.
  - Add a contract test that `/api/health/sync` never writes facts.
  - Ensure UI sync buttons call the durable Open Wearables route.
- **Duplicate-of:** none

### IC-5: Add per-provider data recency to stored Open Wearables facts
- **Type:** Improvement
- **Priority:** medium
- **Where:** app.py `_store_wearable_facts_from_open_wearables`; wearable_fact_store.py
- **Problem:** The bridge stores a single Open Wearables source and derives fact freshness from the fetch result rather than preserving per-provider/event timestamps for each metric. Provider-aware status exists, but stored facts are still coarse.
- **Why it matters:** Recommendations can treat an old metric as fresh if it arrived in a recent metadata fetch.
- **Acceptance criteria:**
  - Store event date/source timestamp per normalized fact when sidecar payload supplies it.
  - Preserve provider identity when the sidecar exposes which provider produced each summary.
  - Recommendation source proof shows provider and metric recency.
  - Facts without reliable event timestamps remain display-only or lower confidence.
- **Duplicate-of:** FIT-245

### IC-6: Productize non-WHOOP managed connector credential setup
- **Type:** Feature
- **Priority:** medium
- **Where:** app.py managed provider credential seeding; Open Wearables setup modal
- **Problem:** Managed sidecar credential seeding is implemented for WHOOP. Other cloud providers rely on sidecar/env readiness and show owner-setup-needed states when credentials are placeholders or missing.
- **Why it matters:** The "Add a wearable" flow is less self-service for Garmin, Oura, Strava, Fitbit, Polar, Suunto, and Ultrahuman.
- **Acceptance criteria:**
  - Define whether each cloud provider is owner-configured, app-assisted, or unsupported.
  - Show provider-specific owner setup instructions without exposing secrets.
  - Add readiness tests for at least one non-WHOOP managed connector.
  - Preserve placeholder detection and restart-required behavior.
- **Duplicate-of:** FIT-245

### IC-7: Add an owner-visible sidecar restart verification path
- **Type:** Improvement
- **Priority:** medium
- **Where:** /api/open-wearables/setup/check; Open Wearables setup modal
- **Problem:** The backend can preserve and clear `managed_connector_restart_required` after hub readiness is observed, but the UI does not provide a concrete restart checklist or proof of which connector change is waiting.
- **Why it matters:** The owner can be blocked by "hub restart needed" without knowing what restart action completed the credential rollout.
- **Acceptance criteria:**
  - Show which managed connector(s) require restart.
  - Provide a safe owner-run restart/check instruction or link to sidecar docs.
  - Clear the block only when hub catalog/authorization proves readiness.
  - Add UI contract coverage for restart-needed and restart-cleared states.
- **Duplicate-of:** FIT-253

### IC-8: Preserve provider identity in recommendation facts where available
- **Type:** Data-contract
- **Priority:** medium
- **Where:** app.py `_store_wearable_facts_from_open_wearables`; recommendation_sources.py
- **Problem:** Current bridge facts are stored under provider id `open_wearables`, even when the sidecar may know the real upstream provider. Provider status lists can be generic, but recommendation facts lose upstream identity.
- **Why it matters:** Source proof is weaker when a coaching adjustment cannot say whether it came from Garmin, Fitbit, Apple Health, or another provider.
- **Acceptance criteria:**
  - Use upstream provider id for facts when sidecar payload safely supplies it.
  - Keep `open_wearables` as the bridge/source label when upstream identity is unavailable.
  - Ensure profile scoping and forbidden-field validation remain unchanged.
  - Add tests for mixed-provider facts and source proof display.
- **Duplicate-of:** FIT-245

### IC-9: Add live sidecar smoke coverage outside unit fixtures
- **Type:** Test
- **Priority:** low
- **Where:** Open Wearables setup/sync routes; open_wearables_adapter.py
- **Problem:** Tests cover the local contracts heavily with mocked sidecar responses, but there is no owner-safe live sidecar smoke procedure in this repo. Real hub catalog, invitation code, and auth payload drift would be caught only during manual operation.
- **Why it matters:** Open Wearables is an external moving part; connector readiness can break even when local unit tests pass.
- **Acceptance criteria:**
  - Define a secret-safe live smoke command or checklist for a local sidecar.
  - Verify provider catalog, setup check, metadata sync, and one blocked/one ready provider path.
  - Store no raw health payloads or secrets in smoke artifacts.
  - Document expected stable error codes for unavailable connectors.
- **Duplicate-of:** none
