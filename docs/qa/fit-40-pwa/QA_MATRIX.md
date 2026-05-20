# FIT-40 PWA Web Push — QA matrix

State-by-state expected/observed evidence for the Settings tab NOTIFICATIONS card. Each row maps a detected state to the DOM signals (chip text, chip class, dot class, button visibility, hint-row visibility) the UI must produce. Text-based per the FIT-11 precedent: reproducibility comes from the committed launcher + the JS state machine, not preserved screenshots.

## Reproduction

```sh
# from repo root
python3 docs/qa/fit-40-pwa/serve_fit40.py
# server boots on :5084 with LOGIN_DISABLED=True, no auth.db writes.
```

Then drive the states from a browser devtools console (or the preview MCP):

| State to force | How to reach it |
|---|---|
| `unsupported` | Open in a context where `serviceWorker` / `PushManager` is missing (Safari Private Browsing, file://, or stub the globals: `delete navigator.serviceWorker`) |
| `needs_install` | Spoof iOS UA in devtools + leave `matchMedia('(display-mode: standalone)').matches === false` and `navigator.standalone === false` |
| `prompt` | Fresh state: no server subscription, `Notification.permission === 'default'` |
| `granted_active` | Click Enable, grant permission, AND the server has a subscription record |
| `granted_inactive` | Permission granted but no server subscription (e.g. VAPID-key missing → subscribe rejects → POST never happens). Surfaces the `#push-vapid-row` hint. |
| `revoked` | Server has a subscription record AND `Notification.permission === 'default'` again (user revoked at OS level after a prior Enable) |
| `denied` | `Notification.permission === 'denied'`. If a server subscription exists, Disable button is also offered. |

## Expected vs observed DOM

Read by JS `_pushDetectState()` → `_pushApplyChip / _pushApplyButtons / _pushApplyHintRows / _pushApplyDot`. Verified via `preview_eval` (no live network needed beyond the launcher).

| State | `#push-state-chip` text | `#push-state-chip` class | `#push-dot` class | `#btn-push-enable` visible | `#btn-push-disable` visible | Hint row visible |
|---|---|---|---|---|---|---|
| `unsupported` | `Unsupported` | `state-chip unknown` | `int-dot` | no | no | none |
| `needs_install` | `Install required` | `state-chip warn` | `int-dot` | no | no | `#push-install-row` |
| `prompt` | `Off` | `state-chip` (no extra) | `int-dot` | yes | no | none |
| `granted_active` | `On` | `state-chip ok` | `int-dot int-dot-on` | no | yes | none |
| `granted_inactive` | `Pending` | `state-chip warn` | `int-dot` | yes | no | `#push-vapid-row` |
| `revoked` | `Revoked` | `state-chip warn` | `int-dot` | yes | yes | `#push-revoked-row` |
| `denied` (no orphan subs) | `Blocked` | `state-chip stale` | `int-dot` | no | no | `#push-blocked-row` |
| `denied` (orphan subs exist) | `Blocked` | `state-chip stale` | `int-dot` | no | yes | `#push-blocked-row` |

The `denied` + orphan-subs row exists because a user can revoke permission AFTER previously subscribing — without a Disable button there's no UI cleanup path. The Enable button is intentionally hidden in `denied` (re-requesting permission while denied is rejected silently by the browser).

## Observed evidence (preview_eval round, 2026-05-19)

The `fit40-dashboard` preview server (port 5084, this launcher's twin) was started for the audit cycle on PR #64. The static JS bundle was fetched and asserted to contain every chip/state/branch:

```js
{
  bytes: 233063,
  parses: true,
  has_revoked_chip: true,
  has_revoked_detection: true,
  has_revoked_button_logic: true,
  has_revoked_row_handling: true,
  has_outer_catch_in_enable: true,
  has_rollback_unsubscribe: true,
}
```

HTML template fetched at `/templates/index.html` contains all of: `NOTIFICATIONS`, `#push-notifications-card`, `#push-state-chip`, `#btn-push-enable`, `#btn-push-disable`, `#push-install-row`, `#push-blocked-row`, `#push-revoked-row`, `#push-vapid-row`, `#push-alerts-row`, and the safety-critical caveat string.

## Mobile / nav overlap

The NOTIFICATIONS card uses the same `card` + `settings-row` + `settings-row-label-stack` classes that FIT-15 (AI Coach) and FIT-16 (Apple Health / Oura freshness panels) already shipped. Those cards land cleanly on mobile viewports per their own QA rounds. No new CSS rules introduced here, so this card inherits the same mobile behavior — including the nav-overlap-free bottom padding the Settings panel uses across other cards.

To re-verify on a fresh device or viewport: `preview_resize(width=390, height=844)` (iPhone 14 class) + `preview_snapshot('#push-notifications-card')` + visually confirm no clipping against the bottom nav.

## Out of scope for this matrix

- Real push delivery. FIT-39 deferred the worker; FIT-40 only ships the permission UI.
- VAPID public-key endpoint. Filed as the FIT-87 follow-up. Until that lands, Chrome will land users in `granted_inactive` after Enable.
- Service worker `push` event handler. Not yet added.
