# Tab Fix: Root Cause Analysis (v2 — FINAL)

**Ticket:** 267e8ab3-752d-49e8-817b-88236454fd2f
**Date:** 2026-04-13
**Status:** FIXED

## Root Causes (FIVE bugs working together)

### Bug 1: Service Worker cache-first served stale JS (CRITICAL — PRIMARY CAUSE)

The SW (`sw.js`) used a **cache-first** strategy for static assets. After the prior tab fix was committed, users on mobile Safari never received the new code — the SW served the old cached `app.js` and `index.html` indefinitely.

The HTML had a SW-unregister snippet at the top, but:
- The snippet was async (`.then()`) and could race with the SW registration
- It unregistered the SW but **did not clear cached responses already in memory**
- Mobile Safari aggressively caches PWA assets independently of the SW

**Fix:** Replaced `sw.js` with a self-destructing SW that unregisters itself on activate, deletes all caches, and forces a page reload. Also monkey-patches `navigator.serviceWorker.register` to prevent future registration.

### Bug 2: `navigateToTab('workout')` undefined — ReferenceError

The "Open Full Plan" button called `onclick="navigateToTab('workout')"` but `navigateToTab` was never defined in any JS file. Clicking this button threw a ReferenceError in the console.

**Fix:** Changed to `onclick="switchTab('workout')"` which is the actual function name.

### Bug 3: Swap overlay z-index blocked bottom nav touches

The swap overlay (`z-index: 1000`) and swap sheet (`z-index: 1001`) shared the same z-index layer as the bottom nav (`z-index: 1000`). On mobile Safari, even with `pointer-events: none` on the hidden overlay, the stacking context could interfere with touch event delivery to the bottom nav buttons.

**Fix:** Lowered swap overlay to `z-index: 900` and swap sheet to `z-index: 910` in both the inline styles and `style.css`.

### Bug 4: Duplicate `switchTab` in app.js + inline script

`app.js` defined `switchTab` and exported it to `window.switchTab`. The inline script at the bottom of `index.html` also defined `switchTab` and overwrote `window.switchTab`. This created a confusing ownership pattern where either version could "win" depending on script execution order, and future edits to one copy would be silently ignored.

**Fix:** Removed `switchTab` from `app.js` entirely. The inline script in `index.html` is now the single canonical definition. Added `history` tab special-case handling (loadHistory/renderMachineFilter) to the inline version since it was previously only in the app.js version.

### Bug 5: Missing cache-busting on CSS/JS assets

The CSS cache buster was `?v=20260311e` (over a month old). Even without the SW, browser caching could serve stale CSS. The JS buster was updated but only once.

**Fix:** Bumped CSS to `?v=20260413b`, JS to `?v=20260413b`. Added `Cache-Control: no-cache, no-store, must-revalidate` meta tags to prevent browser-level caching of the HTML.

## Files Changed

| File | Change |
|------|--------|
| `static/js/sw.js` | Replaced with self-destructing SW that kills all caches |
| `static/js/app.js` | Removed duplicate `switchTab` + its `window.switchTab` export |
| `static/css/style.css` | Swap overlay z-index 1000→900, sheet 1001→910 |
| `templates/index.html` | Nuclear SW kill + register monkey-patch, manifest link fix, CSS/JS cache busters bumped, `navigateToTab` → `switchTab`, added history tab handler to inline switchTab, meta cache-control tags |
| `static/manifest.json` | Created (was missing, caused 404 on every load) |

## Verification Steps (post-deploy)

1. On iPhone Safari: clear all website data for the dashboard domain (Settings → Safari → Advanced → Website Data → Remove)
2. Open dashboard URL
3. Login
4. Tap each tab: Dashboard, Vitals, Next, Log, History, Body, Stats, Health, Settings
5. Each tap should: switch visible content AND highlight the tapped button
6. Tap "Open Full Plan" button on dashboard — should switch to Next Workout tab
7. Verify no JS errors in console (previously: ReferenceError on navigateToTab)
8. Verify Apple Health tab loads data on first tap
9. Verify History tab loads workout list on first tap

## Why the prior fix didn't work

The prior fix (commit 72049ad) correctly added `switchTab` to the HTML, but:
1. The SW served the old cached HTML (no fix at all)
2. Even if the HTML was fresh, `navigateToTab` was still undefined
3. The swap overlay z-index could still block touches on some mobile browsers