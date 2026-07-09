# Push Notifications — PRD

> **Sources:** `README.md`, `docs/VISION.md`, `docs/PRD.md`, `docs/CURRENT_STATE.md`, `app.py`, `data_store.py`, `auth.py`, `templates/index.html`, `static/js/app.js`, `static/js/sw.js`, `static/manifest.json`, `requirements.txt`, `tests/test_push_backend_contract.py`, `tests/test_push_static_contract.py`
> **Routes:** `GET /api/push/vapid-public-key`, `GET /api/push/subscriptions`, `POST /api/push/subscriptions`, `DELETE /api/push/subscriptions/<endpoint_hash>`, `POST /api/push/test`, `GET /api/push/reminders/preview`
> **Generated:** 2026-07-08 (reverse-engineered from code, FIT-268)

## 1. Overview

Push Notifications provide opt-in PWA Web Push setup for low-stakes coaching nudges. The intended reminder categories are stale wearable data and pending food-estimate review. The feature explicitly does not support safety-critical alerts.

The current backend can publish the VAPID public key, save/revoke browser subscriptions, list secret-free subscription summaries, generate a deterministic reminder preview, and send an on-demand test notification through `pywebpush`. The service worker can display a received push payload and focus/open the app on notification click.

Scheduled reminder delivery is not wired in the assigned sources. The reminder preview endpoint returns `delivery: "preview_only"`, and the test payload body says scheduled reminders are not enabled yet. Therefore the real sending surface today is on-demand test push only; reminder rules exist as preview logic and in-app Settings copy.

## 2. User-Facing Surfaces

### Settings -> Notifications

The Notifications settings group contains a "Coaching reminders" card with:

| Element | Behavior |
|---|---|
| `push-state-chip` | Shows current state: unsupported, install required, off, subscribed, needs setup, revoked, or blocked. |
| `push-state-detail` | Explains the current state and setup problem. |
| Enable button | Requests notification permission, waits for service worker readiness, subscribes with VAPID public key, and saves subscription to server; hidden in the denied state because permission cannot be re-requested. |
| Send test button | Visible only when the current device has an active server-backed subscription. Sends a signed server test push. |
| Disable button | Unsubscribes browser push locally and revokes server subscription records; also appears in denied state when orphan server records exist. |
| Install hint row | Shown on iOS when app is not installed to Home Screen. |
| Blocked hint row | Shown when Notification permission is denied. |
| Revoked hint row | Shown when server has an old subscription but browser permission is back to default. |
| VAPID row | Shown when permission is granted but no active subscription exists. |
| Active alerts row | Shows deterministic reminder preview text from `/api/push/reminders/preview`. |
| Test delivery row | Shows setup/test progress and errors. |

### Service Worker Notification

`static/js/sw.js` handles `push` events. It displays:

| Notification field | Source/default |
|---|---|
| Title | `payload.title` or `Fitness Dashboard`. |
| Body | `payload.body` or "Open Fitness Dashboard for the latest coaching reminder." |
| Tag | `payload.tag` or `fitness-dashboard-reminder`. |
| URL | `payload.url` or `/`. |
| Safety critical marker | `payload.safety_critical === true`; current backend payloads set false. |
| Icon/badge | `/static/icons/icon-192.png`. |

Clicking a notification closes it, focuses an existing window for the target URL if present, or opens a new window.

### PWA Manifest

`static/manifest.json` declares standalone display, start URL `/`, app name `FitOS — Training Intelligence`, short name `FitDash`, dark background/theme colors, and 192/512 icons. On iOS, push setup requires the app to be installed to Home Screen.

## 3. Field Inventory

### Push Subscription Save Request

| Field | Type | Required | Default | Validation | Business meaning |
|---|---|---:|---|---|---|
| `subscription` | object | Yes, unless body itself is the subscription | body | Must be object | Browser PushSubscription JSON. |
| `subscription.endpoint` | string | Yes | none | Must start with `https://` | Browser push endpoint. Stored raw for delivery, exposed only as hash/host. |
| `subscription.keys.p256dh` | string | Yes | none | Non-empty | Public ECDH key required by Web Push. Never exposed after save. |
| `subscription.keys.auth` | string | Yes | none | Non-empty | Auth secret required by Web Push. Never exposed after save. |
| `permission_state` | string/null | No | request body value | Not enum-validated | Browser permission state, normally `granted`; used in preview support state. |
| `pwa_installed` | boolean/null | No | request body value | Stored as 1/0/null | Whether browser appears standalone/installed. |
| `user_agent` | string | Server-derived | request header | Not exposed | Server stores request User-Agent for diagnostics. |

### Secret-Free Subscription Summary

| Field | Type | Required | Default | Validation | Business meaning |
|---|---|---:|---|---|---|
| `endpoint_hash` | string | Yes | SHA-256 hex | 64 lowercase hex | Stable id used by UI and revoke/test endpoints. |
| `endpoint_host` | string/null | Yes | parsed from endpoint | Derived | Human-safe endpoint host only. |
| `permission_state` | string/null | No | stored metadata | Raw stored value | Last reported browser permission. |
| `pwa_installed` | boolean/null | No | stored metadata | Derived from integer/null | Whether it was saved from standalone mode. |
| `revoked` | boolean | Yes | false | Derived from `revoked_at` | Whether subscription is inactive. |
| `created_at` | string | Yes | now | SQLite timestamp/ISO | Creation timestamp. |
| `updated_at` | string | Yes | now | ISO seconds when saved/revoked | Sort key; newest first. |
| `keys_present` | boolean | Yes | derived | True if stored JSON has both `p256dh` and `auth` | Confirms server can deliver without exposing keys. |

### Reminder Preview Alert

| Field | Type | Required | Default | Validation | Business meaning |
|---|---|---:|---|---|---|
| `type` | enum | Yes | none | `stale_wearable_data` or `pending_food_estimate_review` | Reminder category. |
| `source` | enum | Yes | none | `oura`, `whoop`, `apple_health`, `food` | Data source needing attention. |
| `title` | string | Yes | source-specific | Constant copy | Notification/in-app title. |
| `body` | string | Yes | source-specific | Constant copy | Notification/in-app detail. |
| `severity` | enum | Yes | `info`/`warning` | `warning` for stale wearable; `info` for aging/missing/pending food | Visual urgency. |
| `status` | string | Yes | source status | From freshness or fixed `pending_review` | Underlying freshness/review state. |
| `last_data_point` | string/null | No | from freshness | None if unavailable | Latest source date. |
| `last_sync_attempt` | string/null | No | from freshness | None if unavailable | Latest sync attempt timestamp. |
| `safety_critical` | boolean | Yes | false | Always false in current preview | Explicit low-stakes contract. |

### Test Push Payload

| Field | Type | Required | Default | Validation | Business meaning |
|---|---|---:|---|---|---|
| `title` | string | Yes | `Fitness Dashboard test` | Constant | Notification title. |
| `body` | string | Yes | "Push notifications are working. Scheduled reminders are not enabled yet." | Constant | Confirms test and scheduled-send gap. |
| `tag` | string | Yes | `fitness-dashboard-test` | Constant | Browser notification replacement key. |
| `url` | string | Yes | `public_base_url()` or `/` | Derived | Click target. |
| `safety_critical` | boolean | Yes | false | Constant | Low-stakes guarantee. |
| `sent_at` | string | Yes | current datetime | ISO seconds | Send timestamp. |

## 4. Interactions & Flows

### Detect Push State

Trigger -> Settings renders or push state changes.
Behavior -> Frontend checks browser support, iOS install state, `Notification.permission`, current server subscriptions, and current browser subscription hash.
Validation -> `serviceWorker`, `PushManager`, and `Notification` must exist; iOS requires standalone display.
API -> `GET /api/push/subscriptions`.
Success -> UI paints state chip, details, buttons, hint rows, and active alert preview.
Failure -> Subscription fetch failure is treated as no server subscriptions; the rest of Settings continues rendering.

### Enable Notifications

Trigger -> User clicks Enable.
Behavior -> UI disables the button, clears prior setup override, requests Notification permission, waits up to 10 seconds for `navigator.serviceWorker.ready`, fetches VAPID public key, subscribes with `userVisibleOnly: true`, posts subscription to server, and rolls back local browser subscription if server save fails.
Validation -> Permission must become `granted`; VAPID public key must be configured; subscription must complete within 10 seconds; server must accept endpoint and keys.
API -> `GET /api/push/vapid-public-key`, then `POST /api/push/subscriptions`.
Success -> UI shows "Notifications enabled. Send a test notification to verify delivery."
Failure -> Shows specific copy for unsupported browser, iOS not installed, permission denied, missing VAPID key, subscribe timeout/failure, network/server save failure, or auth required.

### Disable Notifications

Trigger -> User clicks Disable.
Behavior -> Browser unsubscribe is attempted first, then all active server subscriptions for the user are revoked by endpoint hash.
Validation -> Server validates endpoint hash format on each revoke.
API -> `GET /api/push/subscriptions`, `DELETE /api/push/subscriptions/<endpoint_hash>`.
Success -> Browser stops accepting pushes; server summaries disappear from normal active list.
Failure -> Local unsubscribe or server DELETE failures are logged in console; UI still re-renders.

### Send Test Notification

Trigger -> User clicks Send test.
Behavior -> Frontend confirms current device is `granted_active`, computes the current endpoint hash from the browser subscription, and posts it to the server. Server loads the active raw subscription, builds test payload, signs/sends via `pywebpush`, and returns delivery status.
Validation -> Active subscription must exist on this device and server. Private VAPID key must be configured. `pywebpush` must be installed.
API -> `POST /api/push/test`.
Success -> UI shows "Delivered. This device should show the test notification now." and the service worker displays the notification.
Failure -> 404 means no active subscription; 410 means push service reports subscription gone and server revokes it; 500 means missing private key or missing dependency; other send errors return the push service's own error status (or 500 if none), with status `server_error`. Network failure shows "Not delivered: network or server error."

### Reminder Preview

Trigger -> Settings render or manual call to preview endpoint.
Behavior -> Server computes freshness for Oura, WHOOP, Apple Health, and food. It returns stale/aging/missing wearable alerts and pending-food-review alerts, plus support state from subscriptions and query overrides.
Validation -> WHOOP alerts are suppressed unless WHOOP is connected, needs reauth, or has CSV-imported data; a never-connected account or one holding only historical OAuth-era data after disconnect is ignored.
API -> `GET /api/push/reminders/preview`.
Success -> In-app Active alerts row displays joined alert titles/bodies.
Failure -> UI hides active-alert row.

### Receive and Click Notification

Trigger -> Browser receives a Web Push event.
Behavior -> Service worker parses JSON or falls back to text body, shows notification, and handles click by focusing/opening target URL.
Validation -> None beyond browser push event delivery.
API -> Service worker event, not Flask route.
Success -> User sees notification and can return to app.
Failure -> [TBC] No explicit error reporting exists for failed `showNotification`.

## 5. API Endpoints

| Method | Path | Auth | Trigger | Key params | Response shape | Real/Mock |
|---|---|---|---|---|---|---|
| GET | `/api/push/vapid-public-key` | Session | Enable setup | none | `{public_key}` or 404 `push_vapid_not_configured` | Real |
| GET | `/api/push/subscriptions` | Session | Detect/list state | `include_revoked=true` optional | `{subscriptions:[summary...]}` | Real |
| POST | `/api/push/subscriptions` | Session + CSRF/same-origin | Save browser subscription | Subscription fields above | `{status:"saved", subscription:summary}` | Real |
| DELETE | `/api/push/subscriptions/<endpoint_hash>` | Session + CSRF/same-origin | Disable/revoke | 64-char hex hash | `{status:"revoked"|"not_found", revoked:boolean}` | Real |
| POST | `/api/push/test` | Session + CSRF/same-origin | Send on-demand test | optional `endpoint_hash` | delivered/gone/server_error payload | Real |
| GET | `/api/push/reminders/preview` | Session | In-app alert preview | optional `permission=denied`, `pwa_installed=false` | `{generated_at, alerts, support_state, subscription_count, delivery:"preview_only", safety_critical:false}` | Real preview; no scheduled send |

Endpoint details:

- `GET /api/push/vapid-public-key` reads `FITNESS_PUSH_VAPID_PUBLIC_KEY` first, then `VAPID_PUBLIC_KEY`; missing key returns 404.
- `POST /api/push/subscriptions` accepts either `{subscription:{...}}` or a raw subscription object. It stores raw subscription JSON but returns only a summary.
- `DELETE /api/push/subscriptions/<endpoint_hash>` rejects any hash not matching `[a-f0-9]{64}`.
- `POST /api/push/test` chooses the newest active subscription if no `endpoint_hash` is supplied, but the frontend sends the current device hash.
- If `pywebpush` raises a response with status 404 or 410, the server revokes the subscription and returns HTTP 410.

## 6. Data Model & Persistence

Push subscriptions live in `fitness_data.db`, via `runtime_config.data_path("fitness_data.db")`.

### `push_subscriptions`

| Column | Meaning |
|---|---|
| `id` | Local row id. |
| `user_id` | Owner/profile id. |
| `endpoint_hash` | SHA-256 of endpoint; unique per user and used externally. |
| `endpoint` | Raw HTTPS push endpoint, needed for delivery. |
| `subscription_json` | Raw PushSubscription JSON including keys, needed for delivery. |
| `permission_state` | Last client-reported browser permission state. |
| `pwa_installed` | 1, 0, or null from standalone detection. |
| `user_agent` | Request User-Agent for diagnostics. |
| `revoked_at` | Null when active; timestamp when revoked. |
| `created_at`, `updated_at` | Row timestamps. |

Insert behavior is an upsert on `(user_id, endpoint_hash)`: saving an existing endpoint updates endpoint/subscription metadata, clears `revoked_at`, and updates `updated_at`.

List behavior returns active rows by default and sorts newest first. `include_revoked=true` includes revoked rows.

## 7. Enums & Constants

### Browser/UI Push States

| State | Chip | Meaning |
|---|---|---|
| `unsupported` | Unsupported | Browser lacks service worker, PushManager, or Notification. |
| `needs_install` | Install required | iOS browser is not in standalone/Home Screen mode. |
| `prompt` | Off | Browser permission is default and server has no subscription. |
| `granted_active` | Subscribed | Permission granted and current endpoint hash matches server active subscription. |
| `granted_inactive` | Needs setup | Permission granted but no current server-backed subscription. |
| `revoked` | Revoked | Browser permission default but server still has an active historical subscription. |
| `denied` | Blocked | Browser/OS notification permission denied. |

### Reminder Alert Types

| Type | Meaning |
|---|---|
| `stale_wearable_data` | Oura, WHOOP, or Apple Health data is `aging`, `stale`, or `missing` and relevant. |
| `pending_food_estimate_review` | A food estimate is waiting for review and does not yet count. |

### Freshness Statuses Used by Preview

`aging`, `stale`, and `missing` produce wearable alerts. `stale` yields warning severity; `aging` and `missing` yield info severity. Food uses a boolean `pending_review` state and fixed status `pending_review`.

### Support States

| Value | Meaning |
|---|---|
| `ready` | At least one active subscription and no degradation override. |
| `no_subscription` | No active server subscription. |
| `permission_denied` | Stored subscription says denied or query param `permission=denied`. |
| `not_installed` | Stored subscription says not installed or query param `pwa_installed=false`. |

### VAPID Claims

| Input | Claim result |
|---|---|
| `FITNESS_PUSH_VAPID_SUBJECT` or `VAPID_SUBJECT` set | `{"sub": configured}` |
| Public base URL starts with `https://` | `{"sub": public_base_url}` |
| Otherwise | `{"sub": "mailto:admin@example.com"}` |

## 8. Integration Points

- Freshness system: `_compute_data_freshness()` provides Oura, WHOOP, Apple Health, and food status for reminder preview.
- Food logging: pending food review state produces `pending_food_estimate_review` alerts.
- Wearable integrations: stale Oura/WHOOP/Apple Health data produces preview alerts.
- PWA service worker: registered from `/sw.js`; required for PushManager subscription and notification display.
- Offline service worker fallback returns synthetic 503 JSON `{error:"Offline"}` for failed GET `/api/*` requests and a minimal offline HTML page for failed navigations.
- Runtime public URL: `public_base_url()` feeds test notification click URL and VAPID subject fallback.
- Settings group summary: notification state chip contributes to the Settings notifications group summary.

## 9. Permissions & Security

All push endpoints are session-authenticated by the global auth guard. Mutating endpoints also pass the app's CSRF/same-origin protection (`X-Requested-With: XMLHttpRequest`, valid form CSRF, or same-origin browser metadata). The VAPID public key endpoint is authenticated, even though the key itself is public, because setup is part of the owner app shell.

Raw subscription endpoints and keys are stored server-side for delivery but never returned by list/save responses. Responses expose only endpoint hash, endpoint host, key presence, permission/install metadata, and timestamps.

The feature is explicitly non-safety-critical. Preview and test payloads include `safety_critical: false`; UI copy says reminders are low-stakes and never safety-critical.

## 10. Business Rules

- iOS requires Home Screen installation before enabling push.
- Enable flow must be user-gesture driven because `Notification.requestPermission()` requires it in common browsers.
- Server save failure triggers browser-side unsubscribe rollback to avoid an invisible dangling subscription.
- Disable unsubscribes locally before revoking server records so the browser stops accepting pushes even if DELETE fails.
- The current-device endpoint hash must match a server active subscription before the test button appears.
- Reminder preview is deterministic and non-sending.
- WHOOP missing/stale alerts are suppressed unless WHOOP is connected, needs reauth, or has CSV-imported data.
- Query params can force preview support degradation: `permission=denied`, `pwa_installed=false`.
- Old app caches are deleted by the service worker; the service worker is network-first and does not precache the app shell.

## 11. Config & Environment

| Variable | Default | Meaning |
|---|---|---|
| `FITNESS_PUSH_VAPID_PUBLIC_KEY` | none | Preferred public VAPID key returned to browser. |
| `VAPID_PUBLIC_KEY` | none | Fallback public VAPID key. |
| `FITNESS_PUSH_VAPID_PRIVATE_KEY` | none | Preferred private VAPID key used by server send. |
| `VAPID_PRIVATE_KEY` | none | Fallback private VAPID key. |
| `FITNESS_PUSH_VAPID_SUBJECT` | none | Preferred VAPID subject claim. |
| `VAPID_SUBJECT` | none | Fallback VAPID subject claim. |
| `FITNESS_DASHBOARD_PUBLIC_BASE_URL` | request host URL | Used by `public_base_url()` and HTTPS VAPID subject fallback. |

Dependency:

| Package | Version | Purpose |
|---|---|---|
| `pywebpush` | `2.3.0` | Server-side Web Push delivery. |

## 12. Test Coverage

Existing tests:

- `tests/test_push_backend_contract.py` covers save/list/revoke without exposing keys, validation for missing keys, reminder preview stale/pending-food behavior, WHOOP irrelevance suppression, degraded preview states, public key endpoint, signed test delivery, 410 revocation, and missing private key error.
- `tests/test_push_static_contract.py` covers service worker push/click handling strings, Settings test controls, setup failure copy, render persistence of setup errors, and test-send gating for inactive state.
- `tests/test_fit183_runtime_paths.py` appears in graph results for canonical public base URL feeding Apple Health and push plus VAPID claim fallback behavior.

Coverage gaps:

- [TBC] No test or source evidence shows a scheduler/cron job sending reminder previews as actual pushes.
- [TBC] Browser-level push delivery cannot be fully proven by static tests; service-worker behavior is string/assertion covered but not end-to-end browser verified here.
- [TBC] No retention/rotation policy for old revoked subscription rows was found.

## 13. Gaps & Issue Candidates

### IC-1: Wire scheduled reminder delivery or label preview-only in UI
- **Type:** Feature
- **Priority:** high
- **Where:** `app.py:13891`, `app.py:14090`, `static/js/app.js:6069`
- **Problem:** The app previews stale-data and pending-food reminder alerts, and can send a manual test push, but no scheduled job or trigger sends those reminder alerts as Web Push. UI copy says "no scheduled reminders yet" only in the subscribed detail/test payload, while the card is titled Coaching reminders.
- **Why it matters:** Users may believe reminders are active after subscribing when only test delivery is wired.
- **Acceptance criteria:**
  - Either implement a scheduler/on-demand send path for preview alerts, or rename/copy the UI as preview/test-only.
  - If implemented, sends must be idempotent per alert window and non-safety-critical.
  - Tests prove no sends occur without active subscription and VAPID private key.
- **Duplicate-of:** none

### IC-2: Add subscription retention and cleanup policy
- **Type:** Privacy
- **Priority:** medium
- **Where:** `data_store.py:461`, `data_store.py:198`, `data_store.py:227`
- **Problem:** Revoked subscriptions remain in SQLite indefinitely unless manually cleaned by future code. The active list hides revoked rows, but raw endpoint and key material remain stored.
- **Why it matters:** Push subscription endpoints and auth keys are sensitive delivery material and should not persist forever after revocation.
- **Acceptance criteria:**
  - Define retention window for revoked subscriptions.
  - Add cleanup routine or migration-safe pruning path.
  - Ensure active subscriptions are not accidentally deleted.
  - Add tests for revoked-row pruning and active-row preservation.
- **Duplicate-of:** none

### IC-3: Validate stored permission_state values
- **Type:** Data-contract
- **Priority:** low
- **Where:** `app.py:14028`, `data_store.py:149`
- **Problem:** `permission_state` is stored directly from the request without enum validation. Current UI sends `granted`, but corrupted or unexpected values could distort support-state preview logic.
- **Why it matters:** Notification status should be trustworthy because it controls user setup guidance.
- **Acceptance criteria:**
  - Accept only `granted`, `denied`, `default`, or null.
  - Unknown values are dropped or normalized to null.
  - Tests cover invalid value handling and preview support state.
- **Duplicate-of:** none

### IC-4: Add end-to-end browser QA for push setup and click behavior
- **Type:** Test
- **Priority:** medium
- **Where:** `static/js/app.js:6335`, `static/js/sw.js:29`
- **Problem:** Static tests assert the relevant code exists, but no assigned evidence proves browser permission, service-worker subscription, visible notification, and notification-click focus behavior end to end.
- **Why it matters:** Push is browser/platform-sensitive, especially installed iOS PWA behavior.
- **Acceptance criteria:**
  - Browser QA covers unsupported, denied, iOS not-installed, granted inactive, granted active, and test delivered states.
  - Service-worker notification click is verified to focus/open the expected URL.
  - QA notes platform/browser limitations honestly.
- **Duplicate-of:** none

### IC-5: Redact raw send exception details from push test responses
- **Type:** Privacy
- **Priority:** medium
- **Where:** `app.py:13997`, `app.py:14073`
- **Problem:** Non-410 `pywebpush` exceptions are returned to the client as `error: str(exc)`. Depending on dependency behavior, that may expose provider response details not meant for UI.
- **Why it matters:** Notification delivery errors should be actionable without leaking raw push-service internals.
- **Acceptance criteria:**
  - Return stable public error codes/messages for send failures.
  - Log detailed exception server-side only, without subscription secrets.
  - Tests cover sanitized non-410 exception response.
- **Duplicate-of:** none
